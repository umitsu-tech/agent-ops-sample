"""正解つきの質問集（evals/dataset.jsonl）をエージェントに流し、採点して results/ に残す。

プロンプトやモデル、道具の説明を変えたときに、同じ質問で前後を比べるために使う（オフラインの評価）。
本番のトレースを正解なしで採点するのは evals/run.py。

手元のエージェント（main.py と同じもの）に、1問ずつ新しい会話で投げる。
デプロイした KB を検索するので、.env に KNOWLEDGE_BASE_ID を書いておく。

使い方
    uv run python evals/run_dataset.py           # 全問
    uv run python evals/run_dataset.py 01 05     # 番号を指定
"""

import json
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env")

# langfuse を先に読み込むと、質問集を流したときのトレースも Langfuse に届く（main.py と同じ）
from langfuse import get_client  # noqa: E402

from support_agent.agent import build_agent  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dataset import (  # noqa: E402
    conversation_for_judge,
    extract_tool_calls,
    load_dataset,
    score_by_code,
    summarize,
    tool_calls_for_judge,
)
from jev_judge import judge, judge_correctness  # noqa: E402

# 規程には施行日があるので、質問した日を固定する。罠 5 と 6 は、2026-10-01 施行の改定の前後で答えが分かれる
ASKED_ON = "2026-09-24"
RESULTS_DIR = Path(__file__).resolve().parents[1] / "results"


def ask(question: str) -> tuple[str, list[dict]]:
    """1問を新しい会話で投げ、答えと道具の呼び出しを返す。"""
    agent = build_agent(today=ASKED_ON)
    result = agent(question)
    return str(result).strip(), extract_tool_calls(agent.messages)


def evaluate(item: dict) -> dict:
    """1問を流して採点する。コードで測れるものはコードで、意味の判断は Jev で測る。"""
    answer, tool_calls = ask(item["question"])
    code = score_by_code(item, answer, tool_calls)
    correctness = judge_correctness(item["question"], item["reference"], answer)
    grounding = judge(conversation_for_judge(item["question"], answer), tool_calls_for_judge(tool_calls))
    return {
        "id": item["id"],
        "kind": item["kind"],
        "question": item["question"],
        "answer": answer,
        **code,
        **correctness,
        # 判定（agrees / disagrees）をそのまま合否にする。数値は閾値を変えて見直すために残す
        "correct_ok": correctness["correct_verdict"] == "agrees",
        **grounding,
    }


def _mark(value) -> str:
    return "-" if value is None else ("○" if value else "×")


def save_results(rows: list[dict], summary: dict) -> None:
    """採点結果を手元に残す。Langfuse の無料枠は保持30日なので、比べたい結果はファイルで持っておく。"""
    RESULTS_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    (RESULTS_DIR / f"dataset-{stamp}.json").write_text(
        json.dumps({"asked_on": ASKED_ON, "summary": summary, "rows": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    lines = [f"# 質問集の採点（{stamp}、質問した日 {ASKED_ON}）", ""]
    for name, value in summary.items():
        if isinstance(value, tuple):
            lines.append(f"- {name}: {value[0]} / {value[1]}")
        elif value is not None:
            lines.append(f"- {name}: {value:.2f}" if isinstance(value, float) else f"- {name}: {value}")
    lines += ["", "| # | 種類 | 質問 | 正しさ | 根拠 | 道具 | 引き継ぎ | 出典 |", "|---|---|---|---|---|---|---|---|"]
    for row in rows:
        if "error" in row:
            lines.append(f"| {row['id']} | {row['kind']} | {row['question']} | 失敗: {row['error']} | | | | |")
            continue
        lines.append(
            f"| {row['id']} | {row['kind']} | {row['question']} | "
            f"{_mark(row['correct_ok'])} {row['correct']:.2f}（確信度 {row['correct_confidence']}） | "
            f"{row['grounded']:.2f} {row['grounded_verdict']} | "
            f"{_mark(row['tools_ok'])} {'、'.join(row['tools']) or 'なし'} | "
            f"{_mark(row['escalation_ok'])} {'、'.join(row['escalated_to'])} | {_mark(row['citation_ok'])} |"
        )
    lines += ["", "## 答え", ""]
    for row in rows:
        lines.append(f"- {row['id']}: {row.get('answer', '').replace(chr(10), ' ')}")
    (RESULTS_DIR / f"dataset-{stamp}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n結果を残しました: results/dataset-{stamp}.md")


def main(argv: list[str] | None = None) -> None:
    ids = set(sys.argv[1:] if argv is None else argv)
    items = [item for item in load_dataset() if not ids or item["id"] in ids]

    try:
        langfuse = get_client()
        connected = langfuse.auth_check()
    except Exception as error:  # noqa: BLE001
        langfuse, connected = None, False
        print(f"Langfuse の準備でつまずきました: {error}", file=sys.stderr)

    rows = []
    for item in items:
        try:
            row = evaluate(item)
        except Exception as error:  # noqa: BLE001
            # 1問の失敗（スロットリングなど）で、ほかの問いの結果まで失わないようにする
            row = {"id": item["id"], "kind": item["kind"], "question": item["question"], "error": str(error)}
            print(f"{item['id']}  失敗: {error}", file=sys.stderr)
        else:
            print(
                f"{row['id']}  正しさ {_mark(row['correct_ok'])} {row['correct']:.2f}  根拠 {row['grounded']:.2f}  "
                f"道具 {_mark(row['tools_ok'])}  引き継ぎ {_mark(row['escalation_ok'])}  出典 {_mark(row['citation_ok'])}"
            )
        rows.append(row)

    if langfuse and connected:
        langfuse.flush()
    summary = summarize(rows)
    save_results(rows, summary)
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
