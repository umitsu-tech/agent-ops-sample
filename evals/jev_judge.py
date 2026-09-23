"""Jev にトレースを採点させる。

Jev は文章を返さない。型付きの質問を投げると、当てはまる確率と確信度だけが返る。
1回のリクエストに質問を並べても待ち時間はほとんど変わらないので、観点はまとめて聞く。
"""

import os

import httpx

ENDPOINT = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"

GROUNDED = (
    "The assistant answered an employee's question about company policies using the tools it called. "
    "The tool results contain the policy excerpts it retrieved. "
    "Is every claim in the answer supported by the tool results? "
    "It is supported when the answer only states what the retrieved excerpts contain, "
    "and unsupported when the answer adds amounts, days, conditions or exceptions that do not appear in them, "
    "or applies a rule to a case the excerpts exclude."
)

TOOL_CHOICE = (
    "Look at which tools the assistant called for this employee message. "
    "Were those the right tools? "
    "search_policies is for questions about company rules such as leave, remote work, travel, expenses and IT. "
    "get_application_status is for questions about a specific application number such as EX-2026-0412. "
    "escalate is for judgments about individual cases (leave of absence, side jobs, harassment, late expenses, "
    "exceptions to a rule) and for matters the policies do not cover."
)


def judge(conversation: str, tool_calls: str) -> dict:
    """1つのやりとりを採点して、観点ごとの結果を返す。"""
    api_key = os.environ["TYPESAFE_API_KEY"]

    response = httpx.post(
        ENDPOINT,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": MODEL,
            "state": {"conversation": conversation, "tool_calls": tool_calls},
            "questions": {
                "grounded": {"type": "noul", "instructions": GROUNDED},
                "groundedVerdict": {
                    "type": "choice",
                    "instructions": GROUNDED,
                    "criteria": {
                        "supported": "Every claim comes from the tool results.",
                        "unsupported": "The answer adds something the tool results do not contain.",
                    },
                },
                "toolChoice": {
                    "type": "choice",
                    "instructions": TOOL_CHOICE,
                    "criteria": {
                        "correct": "The tools match what the employee asked about.",
                        "wrong": "Another tool would have been the right one, or a needed tool was not called.",
                    },
                },
            },
        },
        timeout=30.0,
    )
    response.raise_for_status()
    answers = response.json()["answers"]

    return {
        "grounded": answers["grounded"]["noul"],
        "grounded_verdict": answers["groundedVerdict"].get("choice"),
        "grounded_confidence": answers["groundedVerdict"].get("confidence"),
        "tool_choice": answers["toolChoice"].get("choice"),
        "tool_choice_confidence": answers["toolChoice"].get("confidence"),
    }
