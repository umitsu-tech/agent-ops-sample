"""コードで測れるものは、モデルに聞かずにコードで測る。

判定にモデルを使うほどお金と時間がかかるので、ルールで済むものは先に落としておく。
"""

from pathlib import Path


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


def called_expected_tools(tool_names: list[str], expected: list[str]) -> bool:
    """使うべき道具をすべて呼んだか。"""
    return all(name in tool_names for name in expected)


def escalated_as_expected(departments: list[str], expected: list[str]) -> bool:
    """引き継ぐべきときに、正しい部署へ引き継いだか。

    departments は escalate が成功したときの引き継ぎ先の一覧。expected が空なら、引き継がないのが正しい。
    正しい部署に加えて別の部署にも引き継いだときは、誤りにする。
    引き継ぎ先がどれでもよい質問は、呼び出す側で判定から外す。
    """
    if not expected:
        return not departments
    return bool(departments) and all(department in expected for department in departments)


def cited_expected_source(answer: str, expected_sources: list[str]) -> bool:
    """根拠にすべき文書のファイル名を、答えに添えたか。

    拡張子を省いて書かれることもあるので、.md を外した名前で探す。
    """
    return any(Path(source).stem in answer for source in expected_sources)
