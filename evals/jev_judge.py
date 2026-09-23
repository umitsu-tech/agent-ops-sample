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
    "Were those the right tools? Judge only which tools were called, not the arguments passed to them. "
    "search_policies is for questions about company rules such as leave, remote work, travel, expenses and IT. "
    "get_application_status is for questions about a specific application number such as EX-2026-0412. "
    "escalate is for judgments about individual cases (leave of absence, side jobs, harassment, late expenses, "
    "exceptions to a rule) and for matters the policies do not cover."
)


TOOL_ARGS = (
    "Look at the arguments the assistant passed to each tool for this employee message. "
    "Judge only the arguments, not whether the right tools were chosen. "
    "For search_policies, the query should keep the conditions the employee gave, "
    "such as grade, region, employment type, dates and numbers of days. "
    "For get_application_status, the application number should be the one the employee wrote. "
    "For escalate, the department should match the matter: 人事 for leave, remote work, side jobs, "
    "leave of absence, condolence payments, harassment and wages; 総務 for travel, equipment, offices, "
    "information security, PCs and accounts; 経理 for expenses, entertainment, commuting allowance, "
    "corporate cards and advance payments. "
    "Rules kept outside these policies go to 人事, including the rules for executives (such as executive travel), "
    "wages and part-time workers."
)

GOAL = (
    "Did the assistant take care of what the employee needed in this exchange? "
    "It is achieved when the answer gives the employee what they asked for, "
    "or clearly says the policies do not cover it or that the case is for a department to decide, "
    "and hands it over to that department. "
    "It is not achieved when the answer misses the point, leaves the question unanswered, "
    "or leaves the employee to find out by themselves."
)

CORRECT = (
    "An employee asked a question about company policies. "
    "The reference answer was written by the person in charge of the policies and is correct. "
    "Does the assistant's answer agree with the reference answer? "
    "It agrees when it reaches the same conclusion with the same amounts, days and conditions, "
    "even if the wording is different or it adds the source document or a suggestion to contact a department. "
    "Mentioning what applies in other cases is fine as long as the answer for the case asked about matches the reference. "
    "It disagrees when an amount, a number of days, a condition or the conclusion differs from the reference, "
    "when it leaves out the conclusion of the reference, "
    "or when it gives a definite answer where the reference says the policies do not cover the matter "
    "or the case must be handed over to a department."
)


def _ask(state: dict, questions: dict) -> dict:
    """Jev に質問を投げて、質問ごとの答えを返す。"""
    api_key = os.environ["TYPESAFE_API_KEY"]
    response = httpx.post(
        ENDPOINT,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"model": MODEL, "state": state, "questions": questions},
        timeout=30.0,
    )
    response.raise_for_status()
    return response.json()["answers"]


def judge(conversation: str, tool_calls: str) -> dict:
    """1つのやりとりを採点して、観点ごとの結果を返す。"""
    answers = _ask(
        {"conversation": conversation, "tool_calls": tool_calls},
        {
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
            # 道具を呼んでいないと引数は判定できない。Jev は棄権できないので、その選択肢を用意しておく
            "toolArgs": {
                "type": "choice",
                "instructions": TOOL_ARGS,
                "criteria": {
                    "correct": "Every argument matches what the employee said and what the tool needs.",
                    "wrong": "An argument drops or changes a condition, uses the wrong number, or names the wrong department.",
                    "no_tool": "The assistant called no tool.",
                },
            },
            "goal": {"type": "noul", "instructions": GOAL},
            "goalVerdict": {
                "type": "choice",
                "instructions": GOAL,
                "criteria": {
                    "achieved": "The employee got what they needed, or was handed over to the right department.",
                    "not_achieved": "The employee is left without an answer or a next step.",
                },
            },
        },
    )

    return {
        "grounded": answers["grounded"]["noul"],
        "grounded_verdict": answers["groundedVerdict"].get("choice"),
        "grounded_confidence": answers["groundedVerdict"].get("confidence"),
        "tool_choice": answers["toolChoice"].get("choice"),
        "tool_choice_confidence": answers["toolChoice"].get("confidence"),
        "tool_args": answers["toolArgs"].get("choice"),
        "tool_args_confidence": answers["toolArgs"].get("confidence"),
        "goal": answers["goal"]["noul"],
        "goal_verdict": answers["goalVerdict"].get("choice"),
        "goal_confidence": answers["goalVerdict"].get("confidence"),
    }


def judge_correctness(question: str, reference: str, answer: str) -> dict:
    """答えが正解（規程どおりの答え）と合っているかを採点する。

    道具の結果は渡さない。正しさは正解との突き合わせだけで決まるので、判断に要らないものを混ぜないようにする。
    """
    answers = _ask(
        {"question": question, "reference_answer": reference, "assistant_answer": answer},
        {
            "correct": {"type": "noul", "instructions": CORRECT},
            "correctVerdict": {
                "type": "choice",
                "instructions": CORRECT,
                "criteria": {
                    "agrees": "The answer reaches the same conclusion as the reference answer.",
                    "disagrees": "The answer contradicts the reference answer or leaves out its conclusion.",
                },
            },
        },
    )

    return {
        "correct": answers["correct"]["noul"],
        "correct_verdict": answers["correctVerdict"].get("choice"),
        "correct_confidence": answers["correctVerdict"].get("confidence"),
    }
