# agent-ops-sample

技術同人誌の「AI エージェントの運用・評価」の章で使うサンプルです。

オンラインショップの問い合わせ対応エージェントを作り、その振る舞いを観測して採点するところまでを扱います。

- エージェント: Strands Agents + Amazon Bedrock（Nova 2 Lite）
- 観測: Langfuse
- 採点: TypeSafe AI の Jev

## 用意するもの

- Python 3.12 以降と [uv](https://docs.astral.sh/uv/)
- AWS アカウント（Amazon Bedrock で Nova 2 Lite を有効にしておく）
- Langfuse のアカウント（無料の Hobby で足ります）
- TypeSafe AI のアカウント（登録すると試用ぶんのクレジットが付きます）

## 動かす

```bash
cp .env.example .env   # 鍵を書き込む
uv sync
```

エージェントに話しかけます。

```bash
uv run python -m support_agent.main "返品したいんですけど、いつまでにやればいいですか"
```

Langfuse を繋いでいれば、この時点でトレースが記録されます。何度か話しかけたあと、溜まったやりとりを採点します。

```bash
uv run python evals/run.py
```

採点結果は Langfuse のスコアとして書き戻されるので、画面から絞り込めます。

## AgentCore Runtime で動かす

同じエージェントを Amazon Bedrock AgentCore Runtime に載せて、トレースを Langfuse に送ることもできます。デプロイには AgentCore CLI（npm の `@aws/agentcore`。0.22.0 で確認）を使います。

### ローカルと Runtime の違い

入口のファイルと、トレースの送り方が違います。

| | ローカル | Runtime |
|---|---|---|
| 入口 | `src/support_agent/main.py` | `src/support_agent/runtime.py` |
| 送り方 | Langfuse SDK を先に読み込み、SDK に送ってもらう | Strands の `StrandsTelemetry` で Langfuse の OTLP エンドポイントへ直接送る |
| 鍵の渡し方 | `.env` を読む | 起動時に SSM Parameter Store の SecureString から読む |
| 送り切り | 最後に `langfuse.flush()` | 呼び出しのたびに `force_flush()` |

Runtime では呼び出しのたびに送り切ります。送り切る前にセッションが止められると、トレースが欠けるためです。

### ADOT の落とし穴

AgentCore Runtime は既定で ADOT（AWS Distro for OpenTelemetry）を仕込み、トレースを CloudWatch に送ります。ADOT が先に送信先を登録すると、Strands が Langfuse 向けの送信先を設定しようとしても「Overriding of current TracerProvider is not allowed」という警告が出て、トレースは CloudWatch にしか届きません。

そこで `agentcore/agentcore.json` で2か所を設定しています。

- `instrumentation.enableOtel` を `false` にする。Runtime の起動コマンドから `opentelemetry-instrument` が外れます（CLI の既定は `true`）
- 環境変数 `DISABLE_ADOT_OBSERVABILITY=true` を渡す。Runtime が入れる ADOT 用の環境変数が外れます

### Langfuse の鍵の渡し方

鍵は SSM Parameter Store の SecureString に置き、Runtime が起動するときに読みます。`agentcore.json` の `envVars` は平文のままリポジトリにコミットされ、CloudFormation のテンプレートと Runtime の設定にも残るので、鍵はそこに書きません。

Runtime に環境変数として渡すのは、秘密でない次の3つだけです（`agentcore/agentcore.json`）。

| 環境変数 | 値 |
|---|---|
| `DISABLE_ADOT_OBSERVABILITY` | `true` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `https://jp.cloud.langfuse.com/api/public/otel` |
| `LANGFUSE_SSM_PARAMETER` | `/agent-ops-sample/langfuse` |

`runtime.py` は起動時に `LANGFUSE_SSM_PARAMETER` のパラメータを読みます。そこから `Authorization=Basic <base64(公開鍵:秘密鍵)>,x-langfuse-ingestion-version=4` を組み立て、`OTEL_EXPORTER_OTLP_HEADERS` に入れてから送信の設定を作ります。`LANGFUSE_SSM_PARAMETER` が無いとき（手元で動かすとき）は、`.env` から読みます。

パラメータは公開鍵と秘密鍵をまとめた1つの JSON にしています。読み出しが1回で済み、権限も1つのパラメータに絞れるためです。

```json
{"public_key": "pk-lf-...", "secret_key": "sk-lf-..."}
```

Runtime のロールに足している権限は、このパラメータに対する `ssm:GetParameter` だけです（`agentcore/cdk/lib/cdk-stack.ts`）。暗号化は AWS 管理キー `aws/ssm` に任せるので、`kms:Decrypt` は足していません。`aws/ssm` のキーポリシーは、同じアカウントのプリンシパルに Parameter Store 経由の復号を許可しているためです。そのぶん、このアカウントでパラメータを読める人は誰でも復号できます。復号できる人を絞りたいときは、カスタマー管理キーで暗号化し、そのキーへの `kms:Decrypt` を別に付けてください（[SecureString の復号を制限する](https://docs.aws.amazon.com/systems-manager/latest/userguide/ps-restrict-decryption.html)）。

### 鍵を SSM Parameter Store に置く（最初に1回）

リポジトリ直下で実行します。鍵は `.env` から読み、画面にもコマンドラインの引数にも出しません。AWS CLI の `file://` はパイプを読めないので、自分だけが読める一時ファイルを経由し、終わったら消します。

```bash
(
  umask 077
  tmp=$(mktemp)
  trap 'rm -f "$tmp"' EXIT
  uv run python - "$tmp" <<'EOF'
import json, sys
from dotenv import dotenv_values
env = dotenv_values(".env")
value = json.dumps({"public_key": env["LANGFUSE_PUBLIC_KEY"], "secret_key": env["LANGFUSE_SECRET_KEY"]})
with open(sys.argv[1], "w") as f:
    json.dump({"Name": "/agent-ops-sample/langfuse", "Type": "SecureString", "Tier": "Standard", "Value": value}, f)
EOF
  aws ssm put-parameter --region us-east-1 --cli-input-json "file://$tmp"
)
```

表示されるのは `Version` と `Tier` だけです。鍵を入れ替えるときは、`json.dump` に渡す辞書に `"Overwrite": True` を足して、もう一度実行します。

標準のパラメータに料金はかかりません。`aws/ssm` キーがまだ無いリージョンでは、最初の SecureString を作ったときに自動で作られます（AWS 管理キーに月額の料金はかかりません）。

### デプロイする

Node.js 20 以上と AgentCore CLI を用意します。

```bash
npm install -g @aws/agentcore
(cd agentcore/cdk && npm ci)   # CDK の依存を入れる（初回だけ）
export AWS_REGION=us-east-1
aws sts get-caller-identity    # 認証が有効か確かめる
agentcore validate             # 設定ファイルを検証する
agentcore deploy --diff        # 作られるものを確かめる
agentcore deploy -y
```

初回の deploy で、`agentcore/aws-targets.json` に自分のアカウントとリージョンが書き込まれます。CDK の bootstrap が済んでいないリージョンでは、`-y` を付けると bootstrap も自動で行われます。

作られるものは次のとおりです。コードは zip で載せるので、コンテナのビルド（ECR、CodeBuild）は使いません。

- CloudFormation スタック `AgentCore-agentopssample-default`
- AgentCore Runtime `agentopssample_SupportAgent`（DEFAULT エンドポイントは Runtime と一緒に作られます）
- Runtime 用の IAM ロールとポリシー（Bedrock のモデル呼び出し、ログ、`/agent-ops-sample/langfuse` の `ssm:GetParameter` など）
- CDK の bootstrap で作られた S3 バケットに、コードの zip（約 50MB）とテンプレート
- 初めて呼び出したときに、CloudWatch Logs のロググループ `/aws/bedrock-agentcore/runtimes/<RuntimeId>-DEFAULT`

SSM のパラメータは、上の手順で自分で作ったもので、スタックには含まれません。パラメータが無いまま呼び出すと、Runtime は起動の時点で `ParameterNotFound` になって止まります。

### 呼び出す

```bash
agentcore status
agentcore invoke "注文した商品がまだ届きません。注文番号は A-1002 です"
```

トレースは Langfuse の画面で確かめます。CloudWatch 側には出ません。Runtime のログは `agentcore logs` で見られます。

### 手元で Runtime の入口を試す

`agentcore dev` は、条件によっては起動の前に AWS へのデプロイを走らせます（`--skip-deploy` で止められます）。また、手元用のトレース収集を差し込むため、Langfuse への送信とぶつかります。Runtime の入口を試すだけなら、Runtime と同じようにスクリプトとして直接起動するほうが確実です。

`LANGFUSE_SSM_PARAMETER` を設定していなければ、鍵と送り先は `.env` から読むので、SSM のパラメータが無くても動きます。

```bash
uv run python src/support_agent/runtime.py
```

別のターミナルから話しかけます。

```bash
curl -s -X POST http://127.0.0.1:8080/invocations \
  -H 'Content-Type: application/json' \
  -d '{"prompt": "送料はいくらですか"}'
```

### 片付ける

```bash
agentcore remove agent --name SupportAgent -y
agentcore deploy -y                    # Runtime がなくなるので、スタックごと消える
git restore agentcore/agentcore.json   # 設定は元に戻しておく
aws ssm delete-parameter --region us-east-1 --name /agent-ops-sample/langfuse
```

CloudWatch Logs のロググループはスタックとは別に作られるので残ります。要らなければ消してください。

## 中身

```
src/support_agent/tools.py    FAQ 検索・注文照会・引き継ぎの3つの道具
src/support_agent/agent.py    エージェントの定義
src/support_agent/main.py     コマンドラインから1回話しかける
src/support_agent/runtime.py  AgentCore Runtime で動かすときの入口
evals/rules.py                コードで測れるもの
evals/jev_judge.py            Jev に採点させる
evals/run.py                  トレースを取る → 採点する → スコアを書き戻す
data/                         FAQ と注文のモックデータ
agentcore/                    AgentCore CLI の設定（agentcore.json）と CDK
```

## 覚え書き

- `aws login` で認証している場合、boto3 から使うのに `botocore[crt]` が要ります
- Langfuse からトレースを読むときは `fields` で取得する項目を指定します。既定では入出力が付いてこないので `core,basic,io` を指定します
- 採点は何度でも実行できますが、そのたびにスコアが積み上がります。`evals/run.py` は採点済みのトレースを飛ばします
- Runtime に載せる zip は、リポジトリ直下をまるごと固めたものです。`.env` は CLI が外しますが、`results/` のように gitignore しているだけのフォルダは入ります

## ライセンス

MIT
