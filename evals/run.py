"""Langfuse のトレースを取ってきて Jev に採点させ、結果をスコアとして書き戻す。

使い方
    uv run python evals/run.py
"""

import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env")

from langfuse import get_client  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from jev_judge import judge  # noqa: E402

# 取りたい項目はグループで指定する。既定では入出力が付いてこないので io を足す。
FIELDS = "core,basic,io"
# Langfuse の画面にそのまま並ぶ名前。日本語にしておくと読み返すときに楽
SCORE_GROUNDED = "根拠"
SCORE_TOOL_CHOICE = "道具の選択"
LOOKBACK_HOURS = 2
TOOL_OUTPUT_LIMIT = 6000


def _as_text(value) -> str:
    """モデルやツールの入出力を、読める文字列に均す。

    Strands は [{"text": "..."}] の形で渡してくるので、そのままだと括弧が混ざる。
    """
    if value is None:
        return ""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return value
    if isinstance(value, list):
        parts = [_as_text(item) for item in value]
        return " ".join(part for part in parts if part)
    if isinstance(value, dict):
        if "text" in value:
            return _as_text(value["text"])
        if "content" in value:
            return _as_text(value["content"])
    return json.dumps(value, ensure_ascii=False)


def _first_user_message(generation_input) -> str:
    """モデルに渡したメッセージ列から、社員の質問を拾う。"""
    raw = generation_input
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return raw
    if not isinstance(raw, list):
        return _as_text(raw)
    for message in raw:
        if isinstance(message, dict) and message.get("role") == "user":
            return _as_text(message.get("content"))
    return ""


def collect_turns(client, hours: int = LOOKBACK_HOURS) -> dict[str, dict]:
    """直近のトレースを、1トレース1件のまとまりに畳む。"""
    now = datetime.now(timezone.utc)
    response = client.api.observations.get_many(
        from_start_time=now - timedelta(hours=hours),
        to_start_time=now,
        fields=FIELDS,
        limit=100,
    )

    grouped: dict[str, dict] = defaultdict(lambda: {"question": "", "tools": [], "answer": ""})
    for observation in sorted(response.data, key=lambda o: o.start_time):
        bucket = grouped[observation.trace_id]
        if observation.type == "TOOL":
            # 規程の検索は抜粋を何件も返す。短く切ると根拠の採点で「検索結果に無い」と誤判定されるので長めに残す
            bucket["tools"].append(
                {"name": observation.name, "output": _as_text(observation.output)[:TOOL_OUTPUT_LIMIT]}
            )
        elif observation.type == "GENERATION":
            if not bucket["question"]:
                bucket["question"] = _first_user_message(observation.input)
            answer = _as_text(observation.output)
            if answer:
                bucket["answer"] = answer
    return grouped


RESULTS_DIR = Path(__file__).resolve().parents[1] / "results"


def save_results(rows: list[dict]) -> None:
    """採点結果を手元にも残す。

    Langfuse の無料枠はトレースの保持が30日なので、原稿を書いている間に元データが消える。
    引用するつもりの数字は、その場でファイルに落としておく。
    """
    if not rows:
        return
    RESULTS_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")

    (RESULTS_DIR / f"{stamp}.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    header = "| 質問 | 呼んだ道具 | 根拠 | 判定 | 道具の選択 | 確信度 |"
    lines = [header, "|---|---|---|---|---|---|"]
    for row in rows:
        lines.append(
            f"| {row['question']} | {'、'.join(row['tools']) or 'なし'} | "
            f"{row['grounded']:.2f} | {row['grounded_verdict']} | "
            f"{row['tool_choice']} | {row['tool_choice_confidence']} |"
        )
    (RESULTS_DIR / f"{stamp}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n結果を残しました: results/{stamp}.md")


def already_scored(client, trace_id: str) -> bool:
    """このトレースをもう採点したか。

    採点するたびにスコアが積み上がると、画面で同じ行が何度も並んで読めなくなる。
    """
    scored = client.api.scores_v3.get_many_v3(trace_id=trace_id, name=SCORE_GROUNDED, limit=1)
    return len(scored.data) > 0


def main() -> None:
    client = get_client()
    if not client.auth_check():
        print("Langfuse に接続できません。.env を確認してください。", file=sys.stderr)
        sys.exit(1)

    turns = collect_turns(client)
    if not turns:
        print("採点できるトレースがありませんでした。先にエージェントを動かしてください。")
        return

    rows: list[dict] = []
    skipped = 0
    for trace_id, turn in turns.items():
        if not turn["question"]:
            continue
        if already_scored(client, trace_id):
            skipped += 1
            continue

        conversation = f"社員: {turn['question']}\n担当者: {turn['answer']}"
        tool_calls = json.dumps(turn["tools"], ensure_ascii=False)
        verdict = judge(conversation, tool_calls)

        # 数値と判定の両方を残す。あとから閾値を変えて絞り込めるようにするため。
        client.create_score(
            name=SCORE_GROUNDED,
            value=verdict["grounded"],
            trace_id=trace_id,
            data_type="NUMERIC",
            comment=f"{verdict['grounded_verdict']} / 確信度 {verdict['grounded_confidence']}",
        )
        client.create_score(
            name=SCORE_TOOL_CHOICE,
            value=verdict["tool_choice"] or "unknown",
            trace_id=trace_id,
            data_type="CATEGORICAL",
            comment=f"確信度 {verdict['tool_choice_confidence']}",
        )

        rows.append(
            {
                "trace_id": trace_id,
                "question": turn["question"],
                "answer": turn["answer"],
                "tools": [t["name"] for t in turn["tools"]],
                **verdict,
            }
        )
        print(
            f"{trace_id[:12]}  {turn['question'][:28]:<30} "
            f"根拠 {verdict['grounded']:.2f}({verdict['grounded_verdict']}) "
            f"道具 {verdict['tool_choice']}({verdict['tool_choice_confidence']})"
        )

    client.flush()
    if skipped:
        print(f"採点済みだったので飛ばしました: {skipped} 件")
    save_results(rows)


if __name__ == "__main__":
    main()
