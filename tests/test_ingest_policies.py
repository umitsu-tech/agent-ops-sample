"""取り込みスクリプトの単体テスト。AWS には繋がず、botocore の Stubber で bedrock-agent の応答を差し替える。

uv run pytest
"""

import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import boto3
import pytest
from botocore.stub import Stubber

# scripts/ はパッケージではないので、ファイルから読み込む
_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ingest_policies.py"
_spec = importlib.util.spec_from_file_location("ingest_policies", _SCRIPT)
ingest_policies = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ingest_policies)

KB_ID = "KBTEST1234"
DS_ID = "DSTEST1234"
NOW = datetime(2026, 9, 23, tzinfo=timezone.utc)
IDS = {"knowledgeBaseId": KB_ID, "dataSourceId": DS_ID}


def _job(job_id: str, status: str, failed: int = 0) -> dict:
    return {
        **IDS,
        "ingestionJobId": job_id,
        "status": status,
        "statistics": {"numberOfDocumentsScanned": 25, "numberOfNewDocumentsIndexed": 25, "numberOfDocumentsFailed": failed},
        "startedAt": NOW,
        "updatedAt": NOW,
    }


@pytest.fixture
def stubber(monkeypatch):
    """bedrock-agent のクライアントを差し替え、待ち時間を無くす。"""
    monkeypatch.setattr(ingest_policies.time, "sleep", lambda _: None)
    client = boto3.client(
        "bedrock-agent",
        region_name="us-east-1",
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
    )
    with Stubber(client) as stub:
        stub.client = client
        # KB とデータソースを名前で探す
        stub.add_response(
            "list_knowledge_bases",
            {
                "knowledgeBaseSummaries": [
                    {"knowledgeBaseId": "KBOTHER123", "name": "other", "status": "ACTIVE", "updatedAt": NOW},
                    {"knowledgeBaseId": KB_ID, "name": ingest_policies.KNOWLEDGE_BASE_NAME, "status": "ACTIVE", "updatedAt": NOW},
                ]
            },
            {},
        )
        stub.add_response(
            "list_data_sources",
            {"dataSourceSummaries": [{**IDS, "name": "policies", "status": "AVAILABLE", "updatedAt": NOW}]},
            {"knowledgeBaseId": KB_ID},
        )
        yield stub
        stub.assert_no_pending_responses()


def _expect_running_jobs(stub, job_ids: list[str]) -> None:
    stub.add_response(
        "list_ingestion_jobs",
        {"ingestionJobSummaries": [_job(job_id, "IN_PROGRESS") for job_id in job_ids]},
        {**IDS, "filters": [{"attribute": "STATUS", "operator": "EQ", "values": ingest_policies.RUNNING_STATUSES}]},
    )


def _expect_get(stub, job_id: str, status: str, failed: int = 0) -> None:
    stub.add_response("get_ingestion_job", {"ingestionJob": _job(job_id, status, failed)}, {**IDS, "ingestionJobId": job_id})


def test_waits_for_running_job_then_starts_and_waits(stubber):
    """走っている取り込みが終わってから始め、終わるまで待つ。"""
    _expect_running_jobs(stubber, ["OLDJOB1234"])
    _expect_get(stubber, "OLDJOB1234", "IN_PROGRESS")
    _expect_get(stubber, "OLDJOB1234", "COMPLETE")
    stubber.add_response("start_ingestion_job", {"ingestionJob": _job("NEWJOB1234", "STARTING")}, IDS)
    _expect_get(stubber, "NEWJOB1234", "IN_PROGRESS")
    _expect_get(stubber, "NEWJOB1234", "COMPLETE")

    job = ingest_policies.ingest(stubber.client)

    assert job["ingestionJobId"] == "NEWJOB1234"
    assert job["status"] == "COMPLETE"


def test_retries_start_on_conflict(stubber):
    """始める直前に別の取り込みとぶつかったら、待ってからやり直す。"""
    _expect_running_jobs(stubber, [])
    stubber.add_client_error("start_ingestion_job", service_error_code="ConflictException", http_status_code=409)
    _expect_running_jobs(stubber, [])
    stubber.add_response("start_ingestion_job", {"ingestionJob": _job("NEWJOB1234", "STARTING")}, IDS)
    _expect_get(stubber, "NEWJOB1234", "COMPLETE")

    job = ingest_policies.ingest(stubber.client)

    assert job["ingestionJobId"] == "NEWJOB1234"


def test_fails_when_documents_failed(stubber):
    """失敗した文書があれば、取り込みの状態が COMPLETE でも失敗にする。"""
    _expect_running_jobs(stubber, [])
    stubber.add_response("start_ingestion_job", {"ingestionJob": _job("NEWJOB1234", "STARTING")}, IDS)
    _expect_get(stubber, "NEWJOB1234", "COMPLETE", failed=1)

    with pytest.raises(ingest_policies.IngestionError, match="失敗した文書 1 件"):
        ingest_policies.ingest(stubber.client)
