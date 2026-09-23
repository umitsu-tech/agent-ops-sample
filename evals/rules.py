"""コードで測れるものは、モデルに聞かずにコードで測る。

判定にモデルを使うほどお金と時間がかかるので、ルールで済むものは先に落としておく。
"""


def answered_within_limit(answer: str, max_sentences: int = 5) -> bool:
    """答えが約束した長さに収まっているか。"""
    return answer.count("。") <= max_sentences


def used_any_tool(tool_names: list[str]) -> bool:
    """道具を1つも呼ばずに答えていないか。

    何も呼ばずに答えているなら、モデルが記憶だけで答えた可能性がある。
    """
    return len(tool_names) > 0


def escalated_without_reason(tool_names: list[str], reason: str | None) -> bool:
    """理由を書かずに引き継いでいないか。"""
    return "escalate" in tool_names and not (reason or "").strip()
