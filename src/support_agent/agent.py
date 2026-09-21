"""エージェントの定義。"""

import os

from strands import Agent
from strands.models import BedrockModel

from .tools import escalate, get_order_status, search_faq

SYSTEM_PROMPT = """あなたはオンラインショップの問い合わせ対応担当です。

守ること
- 答えは、道具から得た情報だけを根拠にする。知らないことは推測で埋めない
- よくある質問で答えられることは search_faq を使う
- 注文番号が出てきたら get_order_status で調べる
- 返金の可否や商品の不具合など、その場で判断してはいけない内容は escalate で引き継ぐ
- 分からないことは「分かりません」と伝え、必要なら引き継ぐ
- 返事は3文以内で簡潔に
"""

DEFAULT_MODEL_ID = "us.amazon.nova-2-lite-v1:0"


def build_agent(model_id: str | None = None) -> Agent:
    """問い合わせ対応エージェントを組み立てる。"""
    model = BedrockModel(
        region_name=os.environ.get("AWS_REGION", "us-east-1"),
        model_id=model_id or os.environ.get("BEDROCK_MODEL_ID", DEFAULT_MODEL_ID),
    )
    return Agent(
        model=model,
        system_prompt=SYSTEM_PROMPT,
        tools=[search_faq, get_order_status, escalate],
        # 既定では途中経過が標準出力に流れる。ここでは最後の答えだけを扱いたいので止める
        callback_handler=None,
    )
