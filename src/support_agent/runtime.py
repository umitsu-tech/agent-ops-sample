"""Amazon Bedrock AgentCore Runtime で動かすときの入口。

ローカルで試す main.py とは、トレースの送り方が違う。

- main.py: Langfuse SDK を先に読み込み、SDK に送ってもらう
- runtime.py（これ）: Strands の OpenTelemetry の仕組みで、Langfuse の OTLP エンドポイントへ直接送る

Runtime は既定で ADOT（AWS Distro for OpenTelemetry）を仕込み、トレースを CloudWatch に送る。
ADOT が先に送信先を握ると、ここで設定しても「Overriding of current TracerProvider is not allowed」と
警告が出て Langfuse に届かない。そのため agentcore/agentcore.json で ADOT を切っている。

送り先と鍵は環境変数で受け取る。値はデプロイのときに agentcore/cdk/lib/cdk-stack.ts が .env から組み立てる。
- OTEL_EXPORTER_OTLP_ENDPOINT: 例 https://jp.cloud.langfuse.com/api/public/otel
- OTEL_EXPORTER_OTLP_HEADERS: Authorization=Basic <base64(公開鍵:秘密鍵)>,x-langfuse-ingestion-version=4
"""

import sys
from pathlib import Path

# Runtime はこのファイルをスクリプトとして起動する。support_agent をパッケージとして読めるよう src を足しておく
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bedrock_agentcore.runtime import BedrockAgentCoreApp  # noqa: E402
from strands.telemetry import StrandsTelemetry  # noqa: E402

from support_agent.agent import build_agent  # noqa: E402

# エージェントを作る前に送信先を決めておく。Strands は、ここで登録した送信先をそのまま使う
telemetry = StrandsTelemetry()
telemetry.setup_otlp_exporter()

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
