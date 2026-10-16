"""エージェントに持たせるツール。

規程の検索（search_policies）だけが AWS の Knowledge Bases を呼ぶ。申請の照会と引き継ぎは、モックのデータを引くだけ。

docstring はそのままモデルに渡って、いつどのツールを使うかの判断材料になる。
説明が雑だとツールの選択を間違えるので、運用では地味に効く部分。
"""

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

import boto3
from strands import tool

DATA_DIR = Path(__file__).resolve().parents[2] / "data"

# Retrieve で取ってくるチャンクの数。環境変数 KNOWLEDGE_BASE_TOP_K で変えられる
DEFAULT_TOP_K = 5
MAX_TOP_K = 20


def _load(name: str):
    return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _agent_runtime_client():
    """Knowledge Bases を検索するクライアント。呼び出しのたびに作らないよう1つだけ作る。"""
    return boto3.client("bedrock-agent-runtime", region_name=os.environ.get("AWS_REGION", "us-east-1"))


def _top_k() -> int:
    """環境変数から取ってくる件数を読む。おかしな値なら既定値に戻し、範囲に収める。"""
    try:
        value = int(os.environ.get("KNOWLEDGE_BASE_TOP_K", DEFAULT_TOP_K))
    except ValueError:
        value = DEFAULT_TOP_K
    return max(1, min(value, MAX_TOP_K))


def _to_chunk(result: dict) -> dict:
    """Retrieve の結果1件を、本文・スコア・出典だけの形に揃える。

    この形がそのままツールの結果としてトレースに残る。あとで「答えが検索結果に基づいているか」を
    採点するときの材料になるので、本文は切り詰めない。
    """
    uri = result.get("location", {}).get("s3Location", {}).get("uri") or result.get("metadata", {}).get(
        "x-amz-bedrock-kb-source-uri", ""
    )
    return {
        "document": uri.rsplit("/", 1)[-1],
        "source": uri,
        "score": round(float(result.get("score", 0.0)), 4),
        "text": result.get("content", {}).get("text", ""),
    }


def retrieve_chunks(query: str) -> list[dict]:
    """Knowledge Bases の Retrieve を呼び、見つかったチャンクを返す。"""
    knowledge_base_id = os.environ.get("KNOWLEDGE_BASE_ID")
    if not knowledge_base_id:
        raise RuntimeError(
            "環境変数 KNOWLEDGE_BASE_ID が設定されていません。デプロイした Knowledge Base の ID を設定してください"
        )
    response = _agent_runtime_client().retrieve(
        knowledgeBaseId=knowledge_base_id,
        retrievalQuery={"text": query},
        retrievalConfiguration={"vectorSearchConfiguration": {"numberOfResults": _top_k()}},
    )
    return [_to_chunk(result) for result in response.get("retrievalResults", [])]


@tool
def search_policies(query: str) -> dict:
    """社内規程を検索する。

    休暇、在宅勤務、出張、経費精算、交際費、備品の購入、情報セキュリティ、PC の扱いなど、
    社内のルールについての質問に答えるときに使う。申請番号についての質問には使わない。

    Args:
        query: 探したい内容を短い文で。「G4 が大阪に出張するときの宿泊費の上限」のように、条件も含めて書く

    Returns:
        見つかった規程の抜粋。document（文書のファイル名）、source（出典の URI）、score（近さ）、text（本文）の一覧
    """
    chunks = retrieve_chunks(query)
    if not chunks:
        return {"query": query, "results": [], "note": "該当する規程が見つかりませんでした"}
    return {"query": query, "results": chunks}


@tool
def get_application_status(application_id: str) -> dict:
    """申請番号から、経費精算・休暇・出張・副業などの申請の状況を調べる。

    社員が申請番号を伝えてきたときに使う。申請番号は EX-2026-0412 のような形式で、
    EX は経費精算、LV は休暇、BT は出張、SJ は副業・兼業の申請を表す。

    Args:
        application_id: 申請番号

    Returns:
        申請の種類、状況、差し戻しの理由、次にすること。該当する申請が無ければエラーを返す
    """
    applications = _load("applications.json")
    key = application_id.strip().upper()
    return applications.get(key, {"error": f"申請番号 {application_id} は見つかりませんでした"})


# 引き継ぎ先を列挙しておくと、ツールの定義に選択肢として載り、モデルが存在しない部署を作りにくくなる
Department = Literal["人事", "総務", "経理"]


@tool
def escalate(department: Department, reason: str) -> dict:
    """その場で答えてはいけない質問を、担当部署に引き継ぐ。

    個別の事情による判断（休職や復職、副業を許可できるか、ある言動がハラスメントにあたるか、
    期限を過ぎた精算、規程の例外を認めるか）と、規程に書かれていない事項に使う。
    規程を検索して答えられる質問には使わない。

    Args:
        department: 引き継ぎ先。人事（休暇、在宅勤務、副業、休職、慶弔、ハラスメント、賃金）、
            総務（出張、備品、オフィス、情報セキュリティ、PC、アカウント）、
            経理（経費精算、交際費、通勤手当、法人カード、仮払金）のいずれか
        reason: 引き継ぐ理由を一文で

    Returns:
        引き継いだ先と理由
    """
    return {"escalated_to": f"{department}部", "reason": reason}
