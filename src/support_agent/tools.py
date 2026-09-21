"""エージェントに持たせる道具。中身はモックのデータを引くだけ。

docstring はそのままモデルに渡って、いつどの道具を使うかの判断材料になる。
説明が雑だと道具の選択を間違えるので、運用では地味に効く部分。
"""

import json
from pathlib import Path

from strands import tool

DATA_DIR = Path(__file__).resolve().parents[2] / "data"


def _load(name: str):
    return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))


@tool
def search_faq(keyword: str) -> list[dict]:
    """よくある質問を検索する。

    配送、返品、支払い、送料、キャンセルなど、一般的な質問に答えるときに使う。
    特定の注文についての質問には使わない。

    Args:
        keyword: 探したい言葉。「返品」「送料」のような短い単語がよい

    Returns:
        見つかった質問と答えの一覧。最大3件
    """
    faq = _load("faq.json")
    hits = [item for item in faq if keyword in item["question"] or keyword in item["answer"]]
    return hits[:3]


@tool
def get_order_status(order_id: str) -> dict:
    """注文番号から配送状況を調べる。

    お客さまが注文番号を伝えてきたときに使う。注文番号は A-1001 のような形式。

    Args:
        order_id: 注文番号

    Returns:
        配送状況。該当する注文が無ければエラーを返す
    """
    orders = _load("orders.json")
    return orders.get(order_id, {"error": f"注文番号 {order_id} は見つかりませんでした"})


@tool
def escalate(department: str, reason: str) -> dict:
    """自分で答えられない問い合わせを担当部署に引き継ぐ。

    返金の可否、商品の不具合、クレームなど、その場で判断してはいけない内容のときに使う。
    よくある質問や配送状況で答えられるものには使わない。

    Args:
        department: 引き継ぎ先。returns（返品・返金）、quality（品質）、billing（支払い）のいずれか
        reason: 引き継ぐ理由を一文で

    Returns:
        引き継いだ先と理由
    """
    return {"escalated_to": department, "reason": reason}
