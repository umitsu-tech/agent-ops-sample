"""正解つきの質問集（dataset.jsonl）を読み、エージェントの答えを採点する部品。

質問集の1行は、docs/corpus-design.md の罠の表の1件にあたる。

- question: 社員の質問
- reference: 規程どおりの答え。Jev に正しさを採点させるときの正解
- sources: 答えに添えるべき文書のファイル名（どれか1つでよい）。空なら出典は測らない
- tools: 呼ぶべきツール
- escalate_to: 引き継ぐべき部署（どれか1つでよい）。空なら引き継がないのが正しい。null なら引き継ぎは測らない
"""

import json
from pathlib import Path

from rules import called_expected_tools, cited_expected_source, escalated_as_expected

DATASET_PATH = Path(__file__).resolve().parent / "dataset.jsonl"
TOOL_OUTPUT_LIMIT = 6000


def load_dataset(path: Path = DATASET_PATH) -> list[dict]:
    """質問集を読む。空行は飛ばす。"""
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def _content_text(content: list[dict]) -> str:
    """ツールの結果の content を1つの文字列にする。"""
    parts = []
    for block in content:
        if "text" in block:
            parts.append(block["text"])
        elif "json" in block:
            parts.append(json.dumps(block["json"], ensure_ascii=False))
    return "\n".join(parts)


def extract_tool_calls(messages: list[dict]) -> list[dict]:
    """Strands のメッセージ列から、ツールの呼び出しを呼んだ順に取り出す。

    呼び出し（toolUse）は assistant のメッセージに、結果（toolResult）は user のメッセージに入っている。
    toolUseId で突き合わせて、名前・引数・結果・成否（success / error）を1つにまとめる。
    引数がツールの定義に合わないときも、Strands は status を error にして結果を返す。
    """
    calls: dict[str, dict] = {}
    for message in messages:
        for block in message.get("content", []):
            if "toolUse" in block:
                use = block["toolUse"]
                calls[use["toolUseId"]] = {"name": use["name"], "input": use.get("input", {}), "output": "", "status": ""}
            elif "toolResult" in block:
                result = block["toolResult"]
                if result["toolUseId"] in calls:
                    calls[result["toolUseId"]]["output"] = _content_text(result.get("content", []))
                    calls[result["toolUseId"]]["status"] = result.get("status", "")
    return list(calls.values())


def score_by_code(item: dict, answer: str, tool_calls: list[dict]) -> dict:
    """コードで測れるものを測る。測らない観点は None にする。"""
    names = [call["name"] for call in tool_calls]
    # 失敗した引き継ぎ（存在しない部署を渡したなど）は、引き継いだことにしない
    departments = [
        call["input"].get("department", "")
        for call in tool_calls
        if call["name"] == "escalate" and call["status"] == "success"
    ]
    return {
        "tools_ok": called_expected_tools(names, item["tools"]),
        "escalation_ok": None
        if item["escalate_to"] is None
        else escalated_as_expected(departments, item["escalate_to"]),
        "citation_ok": cited_expected_source(answer, item["sources"]) if item["sources"] else None,
        "tools": names,
        "escalated_to": departments,
    }


def tool_args_ok(tool_calls: list[dict], verdict: str | None) -> bool | None:
    """ツールの引数が合っていたか。ツールを呼んだかどうかは、Jev ではなく実際の呼び出しで決める。

    ツールを呼んでいなければ測らない（None）。呼んでいるのに Jev が no_tool と答えたときは、合格にしない。
    """
    if not tool_calls:
        return None
    return verdict == "correct"


def conversation_for_judge(question: str, answer: str) -> str:
    """Jev に渡すやりとりの形にする（evals/run.py と同じ形）。"""
    return f"社員: {question}\n担当者: {answer}"


def tool_calls_for_judge(tool_calls: list[dict]) -> str:
    """Jev に渡すツールの呼び出しの形にする（evals/run.py と同じ形）。検索の抜粋は長いので切る。"""
    return json.dumps(
        [
            {"name": call["name"], "input": call["input"], "output": call["output"][:TOOL_OUTPUT_LIMIT]}
            for call in tool_calls
        ],
        ensure_ascii=False,
    )


def summarize(rows: list[dict]) -> dict:
    """観点ごとに、合っていた件数と測った件数を数える。測らなかった観点（None）と失敗した行は数えない。"""
    scored = [row for row in rows if "error" not in row]

    def count(key: str) -> tuple[int, int]:
        values = [row[key] for row in scored if row.get(key) is not None]
        return sum(1 for value in values if value), len(values)

    grounded = [row["grounded"] for row in scored if row.get("grounded") is not None]
    return {
        "正しさ": count("correct_ok"),
        "ツール": count("tools_ok"),
        "ツールの引数": count("tool_args_ok"),
        "引き継ぎ": count("escalation_ok"),
        "出典": count("citation_ok"),
        "用件の達成": count("goal_ok"),
        "根拠の平均": sum(grounded) / len(grounded) if grounded else None,
        "失敗": len(rows) - len(scored),
    }
