"""コマンドラインから問い合わせを1回投げる。

uv run python -m support_agent.main "G4 で大阪に出張します。1泊いくらまでのホテルに泊まれますか"
"""

import sys

from dotenv import load_dotenv

load_dotenv()

# langfuse を先に読み込むと、トレースの送信先がここで決まる。
# Strands は既にある送信先をそのまま使うので、エージェント側のコードには何も足さなくてよい。
from langfuse import get_client  # noqa: E402

from .agent import build_agent  # noqa: E402

DEFAULT_QUESTION = "G4 で大阪に出張します。1泊いくらまでのホテルに泊まれますか"


def main() -> None:
    question = " ".join(sys.argv[1:]) or DEFAULT_QUESTION

    # 鍵が無くてもエージェント自体は動く。まず動かして、あとから観測を繋ぐ順で読めるようにしている。
    try:
        langfuse = get_client()
        connected = langfuse.auth_check()
    except Exception as error:  # noqa: BLE001
        langfuse, connected = None, False
        print(f"Langfuse の準備でつまずきました: {error}", file=sys.stderr)

    if not connected:
        print("Langfuse に繋がっていません。トレースは送られませんが、このまま動かします。", file=sys.stderr)

    agent = build_agent()
    result = agent(question)
    print(result)

    # 短命なプロセスでは、終わる前に送り切る必要がある
    if langfuse and connected:
        langfuse.flush()


if __name__ == "__main__":
    main()
