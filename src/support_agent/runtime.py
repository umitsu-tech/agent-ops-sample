"""Amazon Bedrock AgentCore Runtime で動かすときの入口。

ローカルで試す main.py とは、トレースの送り方が違う。

- main.py: Langfuse SDK を先に読み込み、SDK に送ってもらう
- runtime.py（これ）: Strands の OpenTelemetry の仕組みで、Langfuse の OTLP エンドポイントへ直接送る

Runtime は既定で ADOT（AWS Distro for OpenTelemetry）を仕込み、トレースを CloudWatch に送る。
ADOT が先に送信先を握ると、ここで設定しても「Overriding of current TracerProvider is not allowed」と
警告が出て Langfuse に届かない。そのため agentcore/agentcore.json で ADOT を切っている。

送り先と鍵の受け取り方
- 送り先: 環境変数 OTEL_EXPORTER_OTLP_ENDPOINT（Runtime では agentcore.json で渡す）
- 鍵: 環境変数 LANGFUSE_SSM_PARAMETER があれば、その名前の SSM パラメータ（SecureString）から起動時に読む。
  無ければ手元で動かしているとみなし、main.py と同じく .env から読む
"""

import base64
import json
import os
import sys
from pathlib import Path

# Runtime はこのファイルをスクリプトとして起動する。support_agent をパッケージとして読めるよう src を足しておく
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import boto3  # noqa: E402
from bedrock_agentcore.runtime import BedrockAgentCoreApp  # noqa: E402
from dotenv import load_dotenv  # noqa: E402
from strands.telemetry import StrandsTelemetry  # noqa: E402

from support_agent.agent import build_agent  # noqa: E402

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


def _keys_from_ssm(parameter_name: str) -> tuple[str, str]:
    """SSM Parameter Store の SecureString から Langfuse の鍵を読む（Runtime 用）。"""
    ssm = boto3.client("ssm", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    value = ssm.get_parameter(Name=parameter_name, WithDecryption=True)["Parameter"]["Value"]
    try:
        keys = json.loads(value)
        return keys["public_key"], keys["secret_key"]
    except (ValueError, KeyError, TypeError):
        # 例外に中身を載せるとログに鍵が残るので、形式が違うことだけを伝える
        raise RuntimeError(
            f'SSM パラメータ {parameter_name} は {{"public_key": ..., "secret_key": ...}} の JSON にしてください'
        ) from None


def _keys_from_dotenv() -> tuple[str, str] | None:
    """手元で動かすときは、main.py と同じく .env から鍵を読む。"""
    load_dotenv(ENV_FILE)
    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY")
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY")
    base_url = os.environ.get("LANGFUSE_BASE_URL")
    if not (public_key and secret_key and base_url):
        return None
    # Runtime では送り先を agentcore.json から渡す。手元では .env の LANGFUSE_BASE_URL から組み立てる
    os.environ.setdefault("OTEL_EXPORTER_OTLP_ENDPOINT", f"{base_url.rstrip('/')}/api/public/otel")
    return public_key, secret_key


def configure_langfuse_headers() -> bool:
    """Langfuse の認証ヘッダーを OTEL_EXPORTER_OTLP_HEADERS に入れる。入れられたら True を返す。"""
    parameter_name = os.environ.get("LANGFUSE_SSM_PARAMETER")
    keys = _keys_from_ssm(parameter_name) if parameter_name else _keys_from_dotenv()
    if keys is None:
        return False

    public_key, secret_key = keys
    auth = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
    # この値には鍵がそのまま入っている。ログや print には出さない
    os.environ["OTEL_EXPORTER_OTLP_HEADERS"] = f"Authorization=Basic {auth},x-langfuse-ingestion-version=4"
    return True


# エージェントを作る前に送信先を決めておく。Strands は、ここで登録した送信先をそのまま使う
telemetry = StrandsTelemetry()
if configure_langfuse_headers():
    # 送り先とヘッダーは、この時点の環境変数から読まれる。だからヘッダーを入れてから呼ぶ
    telemetry.setup_otlp_exporter()
else:
    print("Langfuse の鍵が見つかりません。トレースは送らずに動かします。", file=sys.stderr)

app = BedrockAgentCoreApp()

DEFAULT_QUESTION = "注文した商品がまだ届きません。注文番号は A-1002 です"


@app.entrypoint
def invoke(payload: dict) -> dict:
    """payload の prompt に答える。1回の呼び出しで1問だけ扱う。"""
    question = payload.get("prompt") or DEFAULT_QUESTION

    # main.py と同じく、呼び出しごとに新しいエージェントを作る。前の会話は持ち越さない
    agent = build_agent()
    try:
        result = agent(question)
    finally:
        # トレースは溜めてからまとめて送られる。送り切る前にセッションが止まっても取りこぼさないよう、ここで送り切る
        telemetry.tracer_provider.force_flush()

    return {"result": str(result)}


if __name__ == "__main__":
    app.run()
