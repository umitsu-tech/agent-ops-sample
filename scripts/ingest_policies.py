"""規程を Knowledge Base に取り込み、終わるまで待つ。

デプロイのあと（scripts/deploy.sh の最後）に呼ぶ。単体でも動く。

    uv run python scripts/ingest_policies.py

取り込みは CDK のスタックに入れていない。デプロイの途中で取り込みの完了を待つには自前の Lambda が要るので、
デプロイのあとにこのスクリプトでかける。

- 同じデータソースで取り込みが走っていれば、終わるまで待ってから始める。走っている最中に始めると
  StartIngestionJob が ConflictException になる
- 取り込みは毎回かける。変わっていない文書は Bedrock が飛ばすので、2回目以降は早く終わる
- 取り込みが失敗したときや、失敗した文書があるときは、理由を出して終了コード 1 で終わる
"""

import argparse
import os
import sys
import time

import boto3

# CDK のスタックが付ける名前（agentcore/cdk/lib/policy-knowledge-base.ts）
KNOWLEDGE_BASE_NAME = "AgentCore-agentopssample-default-policies"
DATA_SOURCE_NAME = "policies"

# 取り込みがまだ終わっていない状態
RUNNING_STATUSES = ["STARTING", "IN_PROGRESS", "STOPPING"]
POLL_SECONDS = 5
TIMEOUT_SECONDS = 15 * 60


class IngestionError(Exception):
    """取り込みが失敗した、または時間内に終わらなかった。"""


def find_ids(client, knowledge_base_name: str, data_source_name: str) -> tuple[str, str]:
    """KB とデータソースを名前で探し、それぞれの ID を返す。"""
    knowledge_base_ids = [
        summary["knowledgeBaseId"]
        for page in client.get_paginator("list_knowledge_bases").paginate()
        for summary in page["knowledgeBaseSummaries"]
        if summary["name"] == knowledge_base_name
    ]
    if not knowledge_base_ids:
        raise IngestionError(f"Knowledge Base {knowledge_base_name} が見つかりません。先にデプロイしてください")
    knowledge_base_id = knowledge_base_ids[0]

    data_source_ids = [
        summary["dataSourceId"]
        for page in client.get_paginator("list_data_sources").paginate(knowledgeBaseId=knowledge_base_id)
        for summary in page["dataSourceSummaries"]
        if summary["name"] == data_source_name
    ]
    if not data_source_ids:
        raise IngestionError(f"データソース {data_source_name} が見つかりません")
    return knowledge_base_id, data_source_ids[0]


def wait_for_job(client, knowledge_base_id: str, data_source_id: str, job_id: str, deadline: float) -> dict:
    """取り込みが終わるまで待ち、終わったときの状態を返す。"""
    while True:
        job = client.get_ingestion_job(
            knowledgeBaseId=knowledge_base_id, dataSourceId=data_source_id, ingestionJobId=job_id
        )["ingestionJob"]
        if job["status"] not in RUNNING_STATUSES:
            return job
        if time.monotonic() > deadline:
            raise IngestionError(f"取り込み {job_id} が {TIMEOUT_SECONDS // 60} 分以内に終わりませんでした（{job['status']}）")
        time.sleep(POLL_SECONDS)


def wait_for_running_jobs(client, knowledge_base_id: str, data_source_id: str, deadline: float) -> None:
    """同じデータソースで走っている取り込みがあれば、終わるまで待つ。"""
    pages = client.get_paginator("list_ingestion_jobs").paginate(
        knowledgeBaseId=knowledge_base_id,
        dataSourceId=data_source_id,
        filters=[{"attribute": "STATUS", "operator": "EQ", "values": RUNNING_STATUSES}],
    )
    for page in pages:
        for summary in page["ingestionJobSummaries"]:
            print(f"走っている取り込み {summary['ingestionJobId']} が終わるのを待ちます")
            wait_for_job(client, knowledge_base_id, data_source_id, summary["ingestionJobId"], deadline)


def start_job(client, knowledge_base_id: str, data_source_id: str, deadline: float) -> str:
    """取り込みを始めて、その ID を返す。ほかの取り込みとぶつかったら、それが終わってからやり直す。"""
    while True:
        wait_for_running_jobs(client, knowledge_base_id, data_source_id, deadline)
        try:
            job = client.start_ingestion_job(knowledgeBaseId=knowledge_base_id, dataSourceId=data_source_id)
            return job["ingestionJob"]["ingestionJobId"]
        except client.exceptions.ConflictException:
            # 待ち終わってから始めるまでの間に、別の取り込みが始まったとき
            if time.monotonic() > deadline:
                raise IngestionError("ほかの取り込みが終わらず、取り込みを始められませんでした") from None
            time.sleep(POLL_SECONDS)


def ingest(client, knowledge_base_name: str = KNOWLEDGE_BASE_NAME, data_source_name: str = DATA_SOURCE_NAME) -> dict:
    """取り込みをかけて終わるまで待ち、終わったときの状態を返す。失敗したら IngestionError を投げる。"""
    deadline = time.monotonic() + TIMEOUT_SECONDS
    knowledge_base_id, data_source_id = find_ids(client, knowledge_base_name, data_source_name)
    job_id = start_job(client, knowledge_base_id, data_source_id, deadline)
    print(f"取り込みを始めました: {job_id}")

    job = wait_for_job(client, knowledge_base_id, data_source_id, job_id, deadline)
    stats = job.get("statistics", {})
    failed = stats.get("numberOfDocumentsFailed", 0)
    print(
        f"取り込みが終わりました: {job['status']}"
        f"（読んだ文書 {stats.get('numberOfDocumentsScanned', 0)}、"
        f"新規 {stats.get('numberOfNewDocumentsIndexed', 0)}、"
        f"更新 {stats.get('numberOfModifiedDocumentsIndexed', 0)}、"
        f"削除 {stats.get('numberOfDocumentsDeleted', 0)}、"
        f"失敗 {failed}）"
    )
    if job["status"] != "COMPLETE" or failed > 0:
        reasons = "".join(f"\n  - {reason}" for reason in job.get("failureReasons", []))
        raise IngestionError(f"取り込み {job_id} が失敗しました（{job['status']}、失敗した文書 {failed} 件）{reasons}")
    return job


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="規程を Knowledge Base に取り込み、終わるまで待つ")
    parser.add_argument("--knowledge-base-name", default=KNOWLEDGE_BASE_NAME)
    parser.add_argument("--data-source-name", default=DATA_SOURCE_NAME)
    args = parser.parse_args(argv)

    client = boto3.client("bedrock-agent", region_name=os.environ.get("AWS_REGION", "us-east-1"))
    try:
        ingest(client, args.knowledge_base_name, args.data_source_name)
    except IngestionError as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
