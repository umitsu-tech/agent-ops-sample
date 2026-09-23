"""評価の部品の単体テスト。エージェントも Jev も呼ばない。

uv run pytest
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
# evals/ はパッケージではないので、run.py と同じくパスを足して読み込む
sys.path.insert(0, str(ROOT / "evals"))

import dataset  # noqa: E402

DEPARTMENTS = {"人事", "総務", "経理"}
TOOLS = {"search_policies", "get_application_status", "escalate"}


def test_dataset_points_to_existing_policies_and_known_values():
    """質問集の番号・文書名・道具・引き継ぎ先が、実在するものを指しているか。"""
    items = dataset.load_dataset()
    ids = [item["id"] for item in items]
    assert len(ids) == len(set(ids)) == 26

    policies = {path.name for path in (ROOT / "data" / "policies").glob("*.md")}
    for item in items:
        assert item["question"] and item["reference"], item["id"]
        assert set(item["sources"]) <= policies, item["id"]
        assert set(item["tools"]) <= TOOLS, item["id"]
        assert item["escalate_to"] is None or set(item["escalate_to"]) <= DEPARTMENTS, item["id"]


MESSAGES = [
    {"role": "user", "content": [{"text": "4か月前のタクシー代を今から精算できますか"}]},
    {
        "role": "assistant",
        "content": [
            {"toolUse": {"toolUseId": "t1", "name": "search_policies", "input": {"query": "経費精算の期限"}}},
        ],
    },
    {
        "role": "user",
        "content": [{"toolResult": {"toolUseId": "t1", "status": "success", "content": [{"text": '{"results": []}'}]}}],
    },
    {
        "role": "assistant",
        "content": [
            {"text": "経理部に引き継ぎます。"},
            {"toolUse": {"toolUseId": "t2", "name": "escalate", "input": {"department": "経理", "reason": "期限切れ"}}},
        ],
    },
    {
        "role": "user",
        "content": [{"toolResult": {"toolUseId": "t2", "status": "success", "content": [{"json": {"escalated_to": "経理部"}}]}}],
    },
]


def test_extract_tool_calls_pairs_uses_with_results():
    calls = dataset.extract_tool_calls(MESSAGES)

    assert [call["name"] for call in calls] == ["search_policies", "escalate"]
    assert calls[0]["output"] == '{"results": []}'
    assert calls[1]["input"]["department"] == "経理"
    assert calls[1]["output"] == '{"escalated_to": "経理部"}'


ITEM = {
    "id": "19",
    "question": "4か月前のタクシー代を今から精算できますか",
    "reference": "できない",
    "sources": ["fin-01-expense-reimbursement.md"],
    "tools": ["search_policies"],
    "escalate_to": ["経理"],
}


@pytest.mark.parametrize(
    ("answer", "escalate_to", "sources", "expected"),
    [
        # 引き継ぎ先が合っていて、拡張子を省いたファイル名を添えている
        ("精算できません（fin-01-expense-reimbursement）", ["経理"], ["fin-01-expense-reimbursement.md"], (True, True)),
        # 引き継いではいけない質問で引き継いだ。出典も無い
        ("精算できません", [], ["fin-01-expense-reimbursement.md"], (False, False)),
        # 引き継ぎも出典も測らない
        ("精算できません", None, [], (None, None)),
    ],
)
def test_score_by_code(answer, escalate_to, sources, expected):
    item = {**ITEM, "escalate_to": escalate_to, "sources": sources}

    scores = dataset.score_by_code(item, answer, dataset.extract_tool_calls(MESSAGES))

    assert scores["tools_ok"] is True
    assert (scores["escalation_ok"], scores["citation_ok"]) == expected
    assert scores["escalated_to"] == ["経理"]


def test_summarize_skips_unmeasured_and_failed_rows():
    rows = [
        {"correct_ok": True, "tools_ok": True, "escalation_ok": None, "citation_ok": True, "grounded": 0.9},
        {"correct_ok": False, "tools_ok": True, "escalation_ok": True, "citation_ok": None, "grounded": 0.5},
        {"id": "03", "error": "ThrottlingException"},
    ]

    summary = dataset.summarize(rows)

    assert summary["正しさ"] == (1, 2)
    assert summary["引き継ぎ"] == (1, 1)
    assert summary["出典"] == (1, 1)
    assert summary["根拠の平均"] == pytest.approx(0.7)
    assert summary["失敗"] == 1


class _Observation:
    def __init__(self, type_, name, input_=None, output=None, second=0):
        from datetime import datetime, timezone

        self.trace_id = "trace-1"
        self.type = type_
        self.name = name
        self.input = input_
        self.output = output
        self.start_time = datetime(2026, 9, 23, 0, 0, second, tzinfo=timezone.utc)


def test_collect_turns_keeps_tool_arguments():
    """Langfuse の道具の観測データから、引数を辞書に戻して残す（道具の引数の採点に使う）。"""
    import run

    tool_input = '[{"role":"tool","content":"{\\"department\\": \\"人事\\", \\"reason\\": \\"個別の判断\\"}","id":"t1"}]'
    observations = [
        _Observation("GENERATION", "chat", input_=[{"role": "user", "content": "副業になりますか"}], second=0),
        _Observation("TOOL", "escalate", input_=tool_input, output='{"escalated_to": "人事部"}', second=1),
        _Observation("GENERATION", "chat", output="人事部に引き継ぎました。", second=2),
    ]

    class _Client:
        class api:
            class observations:
                @staticmethod
                def get_many(**_):
                    return type("Response", (), {"data": observations})()

    turn = run.collect_turns(_Client())["trace-1"]

    assert turn["question"] == "副業になりますか"
    assert turn["tools"] == [
        {"name": "escalate", "input": {"department": "人事", "reason": "個別の判断"}, "output": '{"escalated_to": "人事部"}'}
    ]
    assert turn["answer"] == "人事部に引き継ぎました。"
