"""ツールの単体テスト。AWS には繋がず、botocore の Stubber で Retrieve の応答を差し替える。

uv run pytest
"""

import boto3
import pytest
from botocore.stub import Stubber

from support_agent import tools

KNOWLEDGE_BASE_ID = "KBTEST12345"

# Retrieve の応答の形（bedrock-agent-runtime の Retrieve）。Stubber はこの形をサービスの定義と照らし合わせて検査する
RETRIEVE_RESPONSE = {
    "retrievalResults": [
        {
            "content": {"type": "TEXT", "text": "| G4 | 13,000円 | 11,000円 |"},
            "location": {
                "type": "S3",
                "s3Location": {"uri": "s3://docs-bucket/policies/ga-01-domestic-travel.md"},
            },
            "score": 0.61234567,
            "metadata": {
                "x-amz-bedrock-kb-source-uri": "s3://docs-bucket/policies/ga-01-domestic-travel.md",
                "x-amz-bedrock-kb-chunk-id": "chunk-1",
            },
        },
        {
            # location が無くても、metadata の出典から拾えることを確かめる
            "content": {"type": "TEXT", "text": "出張の5営業日前までに出張申請を出し"},
            "score": 0.4,
            "metadata": {"x-amz-bedrock-kb-source-uri": "s3://docs-bucket/policies/ga-01-domestic-travel.md"},
        },
    ]
}


@pytest.fixture
def stubbed_client(monkeypatch):
    """Retrieve を差し替えたクライアントをツールに使わせる。"""
    client = boto3.client(
        "bedrock-agent-runtime",
        region_name="us-east-1",
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
    )
    monkeypatch.setattr(tools, "_agent_runtime_client", lambda: client)
    monkeypatch.setenv("KNOWLEDGE_BASE_ID", KNOWLEDGE_BASE_ID)
    monkeypatch.delenv("KNOWLEDGE_BASE_TOP_K", raising=False)
    with Stubber(client) as stubber:
        yield stubber
        stubber.assert_no_pending_responses()


def _expected_params(query: str, top_k: int) -> dict:
    return {
        "knowledgeBaseId": KNOWLEDGE_BASE_ID,
        "retrievalQuery": {"text": query},
        "retrievalConfiguration": {"vectorSearchConfiguration": {"numberOfResults": top_k}},
    }


def test_search_policies_returns_text_score_and_source(stubbed_client):
    query = "G4 の大阪の宿泊費"
    stubbed_client.add_response("retrieve", RETRIEVE_RESPONSE, _expected_params(query, tools.DEFAULT_TOP_K))

    result = tools.search_policies(query=query)

    assert result["query"] == query
    first, second = result["results"]
    assert first == {
        "document": "ga-01-domestic-travel.md",
        "source": "s3://docs-bucket/policies/ga-01-domestic-travel.md",
        "score": 0.6123,
        "text": "| G4 | 13,000円 | 11,000円 |",
    }
    assert second["source"] == "s3://docs-bucket/policies/ga-01-domestic-travel.md"


def test_search_policies_uses_top_k_from_env(stubbed_client, monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_BASE_TOP_K", "3")
    stubbed_client.add_response("retrieve", {"retrievalResults": []}, _expected_params("住宅手当", 3))

    result = tools.search_policies(query="住宅手当")

    assert result["results"] == []
    assert "見つかりませんでした" in result["note"]


@pytest.mark.parametrize(("raw", "expected"), [("0", 1), ("100", tools.MAX_TOP_K), ("abc", tools.DEFAULT_TOP_K)])
def test_top_k_is_clamped(monkeypatch, raw, expected):
    monkeypatch.setenv("KNOWLEDGE_BASE_TOP_K", raw)
    assert tools._top_k() == expected


def test_search_policies_requires_knowledge_base_id(monkeypatch):
    monkeypatch.delenv("KNOWLEDGE_BASE_ID", raising=False)
    with pytest.raises(RuntimeError, match="KNOWLEDGE_BASE_ID"):
        tools.search_policies(query="有給休暇")


def test_get_application_status_found_and_normalized():
    result = tools.get_application_status(application_id=" lv-2026-0107 ")
    assert result["status"] == "差し戻し"
    assert "2週間前" in result["reason"]


def test_get_application_status_not_found():
    result = tools.get_application_status(application_id="EX-9999-0000")
    assert "error" in result


def test_escalate_lists_departments_in_tool_spec():
    schema = tools.escalate.tool_spec["inputSchema"]["json"]["properties"]["department"]
    assert schema["enum"] == ["人事", "総務", "経理"]
    assert tools.escalate(department="人事", reason="休職の相談") == {"escalated_to": "人事部", "reason": "休職の相談"}
