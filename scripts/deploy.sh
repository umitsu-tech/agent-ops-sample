#!/usr/bin/env bash
# Knowledge Base と Runtime を cdkd でデプロイし、続けて規程を取り込む。
#
#   scripts/deploy.sh [cdkd deploy に渡すオプション]
#
# 取り込みはスタックに入れていない（理由は scripts/ingest_policies.py）。
# デプロイと取り込みを続けて行い、取り込みが終わったところで終わる。
set -euo pipefail

STACK=AgentCore-agentopssample-default
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# このアプリは agentcore/cdk をカレントにして動かす（1つ上の agentcore.json と aws-targets.json を読むため）
cd "$ROOT/agentcore/cdk"
npm run build
cdkd deploy "$STACK" "$@"

cd "$ROOT"
uv run python scripts/ingest_policies.py
