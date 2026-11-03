"""エージェントの定義。"""

import os
from datetime import datetime
from zoneinfo import ZoneInfo

from strands import Agent
from strands.models import BedrockModel

from .tools import escalate, get_application_status, search_policies

SYSTEM_PROMPT = """あなたは株式会社ほしぞら技研の社内規程について、社員からの質問に答える担当です。
今日は {today}（日本時間）です。

守ること
- 答えは、ツールから得た情報だけを根拠にする。一般的な相場、法律の一般論、他社の例で補わない
- 社内のルールについての質問には、まず search_policies で規程を探す。見つからなければ、言い方を変えてもう1回まで探す
- 規程の内容を答えるときは、根拠にした文書のファイル名を添える
- 探しても規程に書かれていないことは「規程には見当たりません」と伝える。金額や日数を推測で言わない
- 等級、地域、雇用形態、日付などで答えが変わるときは、条件ごとに答えるか、質問した人に条件を確認する。施行日や改定日があれば、今日の日付と照らし合わせる
- 申請番号（EX-、LV-、BT-、SJ- で始まるもの）が出てきたら get_application_status で調べる
- 次のものは自分で判断せず、escalate で担当部署に引き継ぐ
  - 個別の事情による判断（休職や復職、副業を許可できるか、ある言動がハラスメントにあたるか、期限を過ぎた精算、規程の例外を認めるか）
  - 規程に書かれていない事項
- 返事は5文以内で簡潔に
"""

DEFAULT_MODEL_ID = "us.amazon.nova-2-lite-v1:0"


def build_system_prompt(today: str | None = None) -> str:
    """今日の日付を埋め込んだシステムプロンプトを作る。

    規程には施行日がある。モデルは今日が何日かを知らないので、日付をこちらから渡しておく。
    """
    today = today or datetime.now(ZoneInfo("Asia/Tokyo")).date().isoformat()
    return SYSTEM_PROMPT.format(today=today)


def build_agent(model_id: str | None = None, today: str | None = None) -> Agent:
    """社内規程の Q&A エージェントを組み立てる。

    today を渡すと、その日に質問されたものとして答える（評価で日付を固定するため）。
    """
    model = BedrockModel(
        region_name=os.environ.get("AWS_REGION", "us-east-1"),
        model_id=model_id or os.environ.get("BEDROCK_MODEL_ID", DEFAULT_MODEL_ID),
    )
    return Agent(
        model=model,
        system_prompt=build_system_prompt(today),
        tools=[search_policies, get_application_status, escalate],
        # 既定では途中経過が標準出力に流れる。ここでは最後の答えだけを扱いたいので止める
        callback_handler=None,
    )
