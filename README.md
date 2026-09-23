# agent-ops-sample

技術同人誌の「AI エージェントの運用・評価」の章で使うサンプルです。

架空の会社の社内規程について質問に答える Q&A エージェントを作り、その振る舞いを観測して採点するところまでを扱います。

- エージェント: Strands Agents + Amazon Bedrock（Nova 2 Lite）
- 規程の検索: Amazon Bedrock Knowledge Bases（ベクトルストアは Amazon S3 Vectors、埋め込みは Titan Text Embeddings V2）
- 実行環境: 手元（`main.py`）と Amazon Bedrock AgentCore Runtime（`runtime.py`）
- インフラ: AgentCore CLI が生成した CDK アプリ（TypeScript）。[cdkd](https://github.com/go-to-k/cdkd) か AgentCore CLI でデプロイします
- 観測: Langfuse
- 採点: TypeSafe AI の Jev

エージェントの道具は3つです。

| 道具 | すること |
|---|---|
| `search_policies` | Knowledge Bases の Retrieve で規程を検索し、見つかったチャンクの本文・スコア・出典を返す |
| `get_application_status` | 経費精算や休暇などの申請の状況を申請番号で調べる（`data/applications.json` のモック） |
| `escalate` | その場で判断してはいけない質問を人事・総務・経理に引き継ぐ（モック） |

規程は `data/policies/` にある25本の Markdown です。根拠のない答えが出やすいように、等級や条件で変わる金額、例外、施行日つきの改定、規程に書かれていない事項を仕込んでいます。一覧と狙いは [docs/corpus-design.md](docs/corpus-design.md) にあります。

## 用意するもの

- Python 3.12 以降と [uv](https://docs.astral.sh/uv/)
- Node.js 22.12 以降（cdkd の要件です。AgentCore CLI だけを使うなら 20 以降）
- AWS アカウント（us-east-1 で確認しています。Amazon Bedrock で Nova 2 Lite と Titan Text Embeddings V2 を使える状態にしておく）
- Langfuse のアカウント（無料の Hobby で足ります）
- TypeSafe AI のアカウント（登録すると試用ぶんのクレジットが付きます）

```bash
cp .env.example .env   # 鍵を書き込む
uv sync
```

## 流れ

1. Langfuse の鍵を SSM Parameter Store に置く（Runtime で動かすときだけ）
2. Knowledge Base と Runtime をデプロイし、規程を取り込む（`scripts/deploy.sh`。AgentCore CLI でもデプロイできる）
3. 手元か Runtime で話しかける
4. 溜まったトレースを採点する。正解つきの質問集を流して採点することもできる
5. 片付ける

## 作られるもの

CDK のスタック `AgentCore-agentopssample-default` に、次のものが入っています（`agentcore/cdk/lib/`）。

| 種類 | 名前 | 役割 |
|---|---|---|
| S3 バケット | 自動で付く名前 | 規程の原本を `policies/` に置く。スタックを消すと中身ごと消える |
| アップロード（BucketDeployment） | - | `data/policies/` をバケットに置く。Lambda と Lambda レイヤーを使う |
| S3 Vectors のベクトルバケット | `agentcore-agentopssample-default-policies` | ベクトルの置き場 |
| S3 Vectors のインデックス | `policies` | 1024 次元、cosine |
| IAM ロール | 自動で付く名前 | KB が埋め込みモデル・インデックス・規程のバケットを使うためのロール |
| Knowledge Base | `AgentCore-agentopssample-default-policies` | 埋め込みは Titan Text Embeddings V2 |
| データソース | `policies` | 規程のバケットの `policies/` を固定長（300 トークン、重なり 20%）で取り込む |
| AgentCore Runtime | `agentopssample_SupportAgent` | エージェント本体。環境変数 `KNOWLEDGE_BASE_ID` と、その KB への `bedrock:Retrieve` を渡している |

規程の取り込み（StartIngestionJob）はスタックに入れず、デプロイのあとに `scripts/ingest_policies.py` でかけます。デプロイの途中で取り込みの完了を待つには、自前の Lambda が要るためです（「[規程を取り込む](#規程を取り込む)」）。

AgentCore CLI の `agentcore.json` にも `knowledgeBases` がありますが、0.22.0 が作る KB は `MANAGED` 型で、ベクトルストアを選べません。S3 Vectors を使うため、KB は CDK に自分で書いています（`agentcore/cdk/lib/policy-knowledge-base.ts`）。

### Runtime の zip に入れるもの

Runtime に載せる zip には、`pyproject.toml`、`src/`、`data/applications.json` だけを入れます（`agentcore/cdk/lib/runtime-code.ts`）。

AgentCore CLI 0.22.0（`@aws/agentcore-cdk` 0.1.0-alpha.53）の CodeZip は、コードの置き場（ここではリポジトリ直下）を `.git` や `.venv` などを除いてほぼそのまま固めます。`.env` は除かれません。そのままだと、鍵の入った `.env` が zip に入り、アセット用の S3 バケットと Runtime に載ってしまいます。そこで、スタックの定義で必要なものだけを `agentcore/.cache/runtime-code/` に写し、そこを固めさせています。

## 料金の目安

待機中にかかる固定費は、保存しているデータの料金だけです。OpenSearch Serverless のように、使っていなくても時間単位でかかる料金はありません。

- S3 Vectors: ベクトルの保存量、書き込み（取り込みのとき）、検索の回数に応じてかかります。規程は25本なので、保存量はごくわずかです
- Titan Text Embeddings V2: 取り込みと検索のときに、埋め込んだトークン数に応じてかかります
- Nova 2 Lite: エージェントが考えるたびに、入出力のトークン数に応じてかかります
- AgentCore Runtime: 呼び出しで動いている間の CPU とメモリに応じてかかります
- S3（規程、アセット、cdkd の状態）、Lambda（デプロイのときだけ動く）、CloudWatch Logs: 使ったぶんだけです。SSM の標準パラメータは無料です

料金の詳細は [Amazon S3 Vectors](https://aws.amazon.com/s3/pricing/)、[Amazon Bedrock](https://aws.amazon.com/bedrock/pricing/)、[Amazon Bedrock AgentCore](https://aws.amazon.com/bedrock/agentcore/pricing/) の料金ページを確認してください。

## Langfuse の鍵を SSM Parameter Store に置く（最初に1回）

Runtime で動かすときは、鍵を SSM Parameter Store の SecureString に置き、Runtime が起動するときに読みます。手元で `main.py` を動かすだけなら要りません。

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

パラメータが無いまま Runtime を呼び出すと、Runtime は起動の時点で `ParameterNotFound` になって止まります。

## cdkd でデプロイする

cdkd は、CDK アプリを CloudFormation を通さずに AWS の API で直接デプロイするツールです。スタックの状態は、自分のアカウントの S3 バケットに置きます。0.290.49 で確認しています。

### 最初に1回

```bash
npm install -g @go-to-k/cdkd
cd agentcore/cdk
npm ci
export AWS_REGION=us-east-1
aws sts get-caller-identity   # 認証が有効か確かめる
cdkd bootstrap --region us-east-1
```

`cdkd bootstrap` が作るものは次のとおりです。IAM ロールは作りません。

| 種類 | 名前 | 中身 |
|---|---|---|
| S3 バケット | `cdkd-state-<アカウントID>` | スタックの状態。アカウントに1つ。バージョニングと SSE-S3 が有効で、ほかのアカウントからの操作を拒否するバケットポリシーが付く |
| S3 バケット | `cdkd-assets-<アカウントID>-us-east-1` | アセット（Runtime の zip、Lambda のコードなど）。SSE-S3、パブリックアクセスはすべてブロック |
| ECR リポジトリ | `cdkd-container-assets-<アカウントID>-us-east-1` | コンテナのアセット用。このサンプルでは使わない（空のまま） |

CDK の `cdk bootstrap`（CDKToolkit スタック）は要りません。

### デプロイする

コマンドは `agentcore/cdk` で実行します。このアプリは、カレントディレクトリの1つ上にある `agentcore.json` と `aws-targets.json` を読むためです。

```bash
cd agentcore/cdk
npm run build   # cdk.json の app は、ビルド済みの dist/bin/cdk.js を動かす

# デプロイ先を書く。agentcore deploy なら初回に自動で書かれるが、cdkd では自分で書く（コミットはしない）
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
echo "[{\"name\":\"default\",\"account\":\"$ACCOUNT\",\"region\":\"us-east-1\"}]" > ../aws-targets.json

cdkd synth                                   # 手元で合成するだけ。AWS には何も作らない
cdkd diff AgentCore-agentopssample-default   # 作られるものを確かめる

cd ../..
scripts/deploy.sh   # ビルド → cdkd deploy → 規程の取り込み（終わるまで待つ）
```

`scripts/deploy.sh` は、`npm run build`、`cdkd deploy AgentCore-agentopssample-default`、`scripts/ingest_policies.py` を続けて実行します。引数を付けると `cdkd deploy` に渡します。

合成のときに、`uv` が Runtime の依存を Linux 向けに入れて zip を作ります（約 55MB）。最後に Outputs として、KB の ID（`PolicyKnowledgeBaseId`）などが表示されます。あとから見るときは `cdkd state show AgentCore-agentopssample-default` を使います。

cdkd での扱いは次のとおりです（0.290.49 のソースとドキュメントで確認）。

| リソースの種類 | cdkd での扱い |
|---|---|
| `AWS::BedrockAgentCore::Runtime` | AgentCore CLI の CDK がタグを付けるため、Cloud Control API 経由になる |
| `AWS::S3Vectors::VectorBucket` | cdkd の専用の処理。名前が必須なので、CDK で名前を付けている |
| `AWS::S3Vectors::Index`、`AWS::Bedrock::KnowledgeBase`、`AWS::Bedrock::DataSource` | Cloud Control API 経由 |
| `AWS::IAM::*`、`AWS::S3::*`、`AWS::Lambda::*`、`AWS::Logs::LogGroup` | cdkd の専用の処理 |
| `Custom::CDKBucketDeployment`、`Custom::S3AutoDeleteObjects` | cdkd がカスタムリソースの Lambda を呼び、応答を待つ |

Runtime を Cloud Control API 経由で作れないときは、`scripts/deploy.sh --prefer-sdk-route AWS::BedrockAgentCore::Runtime:Tags` のように `cdkd deploy` へオプションを渡すと cdkd の専用の処理で作れます（そのかわり Runtime にタグが付きません）。

### 規程を取り込む

`scripts/deploy.sh` は、デプロイのあとに `scripts/ingest_policies.py` で規程を取り込みます。取り込みだけをやり直すときは、リポジトリ直下で次を実行します。

```bash
uv run python scripts/ingest_policies.py
```

スクリプトは KB とデータソースを名前で探し、次の順に進めます。

- 同じデータソースで取り込みが走っていれば、終わるまで待つ。走っている最中に始めると `StartIngestionJob` が `ConflictException` になるため
- 取り込みを始め、終わるまで待つ（15分まで）
- 取り込みが失敗したときや、失敗した文書があるときは、理由を出して終了コード 1 で終わる

初回は `取り込みが終わりました: COMPLETE（読んだ文書 25、新規 25、更新 0、削除 0、失敗 0）` のように出ます。取り込みは毎回かけますが、変わっていない文書は Bedrock が飛ばすので、2回目以降は変わった文書だけが数えられ、早く終わります。

### 呼び出す

cdkd でデプロイしたときは、`agentcore invoke` と `agentcore status` は使えません。AgentCore CLI は自分のデプロイの記録（`agentcore/.cli/deployed-state.json`）を見るので、cdkd で作った Runtime を見つけられないためです。AWS CLI で呼び出します。

```bash
RUNTIME_ARN=$(aws bedrock-agentcore-control list-agent-runtimes \
  --query "agentRuntimes[?agentRuntimeName=='agentopssample_SupportAgent'].agentRuntimeArn" --output text)
aws bedrock-agentcore invoke-agent-runtime \
  --agent-runtime-arn "$RUNTIME_ARN" \
  --content-type application/json \
  --cli-binary-format raw-in-base64-out \
  --payload '{"prompt": "G4 で大阪に出張します。1泊いくらまでのホテルに泊まれますか"}' \
  response.json
cat response.json
```

トレースは Langfuse の画面で確かめます。

### 片付ける

```bash
cd agentcore/cdk
cdkd destroy AgentCore-agentopssample-default
git restore ../aws-targets.json
aws ssm delete-parameter --region us-east-1 --name /agent-ops-sample/langfuse
```

cdkd は、規程のバケットを空にしてから消します。ベクトルバケットは、中のインデックスを消してから消します。途中で止まってベクトルバケットが残ったときは、次のようにインデックスから消します（ベクトルバケットは空でないと消せません）。

```bash
aws s3vectors list-indexes --vector-bucket-name agentcore-agentopssample-default-policies
aws s3vectors delete-index --vector-bucket-name agentcore-agentopssample-default-policies --index-name policies
aws s3vectors delete-vector-bucket --vector-bucket-name agentcore-agentopssample-default-policies
```

スタックとは別に、呼び出したときに作られる CloudWatch Logs のロググループ（`/aws/bedrock-agentcore/runtimes/<RuntimeId>-DEFAULT` と、バケットを空にする Lambda の `/aws/lambda/...`）が残ります。要らなければ消してください。

cdkd 自身のバケットと ECR リポジトリも消すときは、次を実行します。

```bash
cdkd bootstrap --destroy --region us-east-1 --include-state-bucket
```

## AgentCore CLI でデプロイする（もう1つの方法）

同じ CDK アプリを、AgentCore CLI（npm の `@aws/agentcore`。0.22.0 で確認）で CloudFormation のスタックとしてデプロイすることもできます。

```bash
npm install -g @aws/agentcore
(cd agentcore/cdk && npm ci)
export AWS_REGION=us-east-1
aws sts get-caller-identity
agentcore validate          # 設定ファイルを検証する
agentcore deploy --diff     # 作られるものを確かめる
agentcore deploy -y
uv run python scripts/ingest_policies.py   # 規程を取り込む（終わるまで待つ）
```

初回の deploy で、`agentcore/aws-targets.json` に自分のアカウントとリージョンが書き込まれます。CDK の bootstrap が済んでいないリージョンでは、`-y` を付けると bootstrap（CDKToolkit スタックと、アセット用の S3 バケットなど）も自動で行われます。規程の取り込みは cdkd のときと同じく、デプロイのあとに `scripts/ingest_policies.py` でかけます。

```bash
agentcore status
agentcore invoke "G4 で大阪に出張します。1泊いくらまでのホテルに泊まれますか"
```

片付けるときは、エージェントを設定から外してもう一度デプロイします。リソースが無くなるので、CLI が CloudFormation のスタックごと消します（KB やベクトルバケットも消えます）。

```bash
agentcore remove agent --name SupportAgent -y
agentcore deploy -y
git restore agentcore/agentcore.json agentcore/aws-targets.json
aws ssm delete-parameter --region us-east-1 --name /agent-ops-sample/langfuse
```

同じアカウントとリージョンで、cdkd と AgentCore CLI の両方からデプロイしないでください。Runtime や KB の名前がぶつかります。

## 手元で話しかける

手元の `main.py` も、デプロイした Knowledge Base を検索します。`.env` の `KNOWLEDGE_BASE_ID` に、デプロイで出力された `PolicyKnowledgeBaseId` を書いてから実行します。手元の AWS の認証情報には、その KB への `bedrock:Retrieve` が要ります。

```bash
uv run python -m support_agent.main "G4 で大阪に出張します。1泊いくらまでのホテルに泊まれますか"
```

Langfuse を繋いでいれば、この時点でトレースが記録されます。検索した規程の本文・スコア・出典は、`search_policies` の結果としてトレースに残ります。

### 手元で Runtime の入口を試す

`agentcore dev` は、条件によっては起動の前に AWS へのデプロイを走らせます（`--skip-deploy` で止められます）。また、手元用のトレース収集を差し込むため、Langfuse への送信とぶつかります。Runtime の入口を試すだけなら、スクリプトとして直接起動するほうが確実です。

`LANGFUSE_SSM_PARAMETER` を設定していなければ、鍵と送り先と `KNOWLEDGE_BASE_ID` は `.env` から読むので、SSM のパラメータが無くても動きます。

```bash
uv run python src/support_agent/runtime.py
```

別のターミナルから話しかけます。

```bash
curl -s -X POST http://127.0.0.1:8080/invocations \
  -H 'Content-Type: application/json' \
  -d '{"prompt": "今月は在宅勤務を7日しました。手当はいくらですか"}'
```

## 採点する

何度か話しかけたあと、溜まったやりとりを採点します。

```bash
uv run python evals/run.py
```

直近2時間のトレースを Langfuse から取り、1つのやりとりごとに Jev に4つの観点を聞きます。正解は使いません。

| スコア名 | 聞くこと | 型 |
|---|---|---|
| 根拠 | 答えが道具の結果だけに基づいているか | 数値（noul）。判定と確信度をコメントに入れる |
| 道具の選択 | 呼んだ道具が質問に合っているか（引数は見ない） | カテゴリ（correct / wrong） |
| 道具の引数 | 道具に渡した引数が合っているか。検索の言葉が等級や地域などの条件を落としていないか、申請番号や引き継ぎ先が合っているか | カテゴリ（correct / wrong / no_tool） |
| 用件の達成 | 質問した人の用件が片付いたか。答えたか、規程に無いことや個別の判断を担当部署に引き継いだか | 数値（noul）。判定と確信度をコメントに入れる |

道具の選択と引数を分けているのは、1つの質問にまとめると、どちらで間違えたのかが分からなくなるためです。道具を1つも呼ばなかったやりとりでは、Jev は引数について棄権できないので、`no_tool` という選択肢を用意しています。

採点結果は Langfuse のスコアとして書き戻されるので、画面から絞り込めます。同じトレースに同じスコアが積み上がらないよう、すでに付いている観点は飛ばし、付いていない観点だけを書き戻します。観点を足す前に採点したトレースにも、足した観点だけが付きます。

### 正解つきの質問集で採点する

プロンプトやモデル、道具の説明を変えたときは、同じ質問で前後を比べます。`evals/dataset.jsonl` に、罠の表（[docs/corpus-design.md](docs/corpus-design.md)）を元にした26問と、それぞれの正解・根拠の文書・引き継ぎ先を置いています。

```bash
uv run python evals/run_dataset.py           # 全問
uv run python evals/run_dataset.py 01 05     # 番号を指定
```

手元のエージェントに1問ずつ新しい会話で投げ、次の5つと、上の道具の引数・用件の達成を測って `results/dataset-<日時>.md` に残します。デプロイした KB を検索するので、先に `.env` の `KNOWLEDGE_BASE_ID` を書いておきます。質問した日は 2026-09-24 に固定しています（規程の施行日で答えが変わる質問があるため）。

| 観点 | 測り方 |
|---|---|
| 正しさ | Jev。質問・正解・答えを渡し、正解と合っているかを聞く |
| 根拠 | Jev。`evals/run.py` と同じ |
| 道具 | コード。使うべき道具を呼んだか |
| 引き継ぎ | コード。引き継ぐべき質問で正しい部署に引き継いだか、引き継がなくてよい質問で引き継いでいないか |
| 出典 | コード。根拠の文書のファイル名を答えに添えたか |

26問で、Nova 2 Lite の呼び出しが26回と、Jev のリクエストが52回です。トレースは Langfuse にも届きます。

## テスト

```bash
uv run pytest                     # 道具・取り込みスクリプト・採点の部品の単体テスト（AWS の応答は botocore の Stubber で差し替える）
(cd agentcore/cdk && npx jest)    # CDK のテスト
```

## ローカルと Runtime の違い

入口のファイルと、トレースの送り方が違います。

| | ローカル | Runtime |
|---|---|---|
| 入口 | `src/support_agent/main.py` | `src/support_agent/runtime.py` |
| 送り方 | Langfuse SDK を先に読み込み、SDK に送ってもらう | Strands の `StrandsTelemetry` で Langfuse の OTLP エンドポイントへ直接送る |
| 鍵の渡し方 | `.env` を読む | 起動時に SSM Parameter Store の SecureString から読む |
| KB の ID | `.env` の `KNOWLEDGE_BASE_ID` | CDK のスタックが環境変数 `KNOWLEDGE_BASE_ID` で渡す |
| 送り切り | 最後に `langfuse.flush()` | 呼び出しのたびに `force_flush()` |

Runtime では呼び出しのたびに送り切ります。送り切る前にセッションが止められると、トレースが欠けるためです。

### ADOT の落とし穴

AgentCore Runtime は既定で ADOT（AWS Distro for OpenTelemetry）を仕込み、トレースを CloudWatch に送ります。ADOT が先に送信先を登録すると、Strands が Langfuse 向けの送信先を設定しようとしても「Overriding of current TracerProvider is not allowed」という警告が出て、トレースは CloudWatch にしか届きません。

そこで `agentcore/agentcore.json` で2か所を設定しています。

- `instrumentation.enableOtel` を `false` にする。Runtime の起動コマンドから `opentelemetry-instrument` が外れます（CLI の既定は `true`）
- 環境変数 `DISABLE_ADOT_OBSERVABILITY=true` を渡す。Runtime が入れる ADOT 用の環境変数が外れます

### Langfuse の鍵の渡し方

`agentcore.json` の `envVars` は平文のままリポジトリにコミットされ、CloudFormation のテンプレートと Runtime の設定にも残るので、鍵はそこに書きません。

Runtime に環境変数として渡すのは、秘密でない次の4つだけです。

| 環境変数 | 値 | 渡しているところ |
|---|---|---|
| `DISABLE_ADOT_OBSERVABILITY` | `true` | `agentcore/agentcore.json` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `https://jp.cloud.langfuse.com/api/public/otel` | `agentcore/agentcore.json` |
| `LANGFUSE_SSM_PARAMETER` | `/agent-ops-sample/langfuse` | `agentcore/agentcore.json` |
| `KNOWLEDGE_BASE_ID` | デプロイした KB の ID | `agentcore/cdk/lib/cdk-stack.ts` |

`runtime.py` は起動時に `LANGFUSE_SSM_PARAMETER` のパラメータを読みます。そこから `Authorization=Basic <base64(公開鍵:秘密鍵)>,x-langfuse-ingestion-version=4` を組み立て、`OTEL_EXPORTER_OTLP_HEADERS` に入れてから送信の設定を作ります。`LANGFUSE_SSM_PARAMETER` が無いとき（手元で動かすとき）は、`.env` から読みます。

パラメータは公開鍵と秘密鍵をまとめた1つの JSON にしています。読み出しが1回で済み、権限も1つのパラメータに絞れるためです。

```json
{"public_key": "pk-lf-...", "secret_key": "sk-lf-..."}
```

Runtime のロールに足している権限は、このパラメータに対する `ssm:GetParameter` と、KB に対する `bedrock:Retrieve` だけです（`agentcore/cdk/lib/cdk-stack.ts`）。暗号化は AWS 管理キー `aws/ssm` に任せるので、`kms:Decrypt` は足していません。`aws/ssm` のキーポリシーは、同じアカウントのプリンシパルに Parameter Store 経由の復号を許可しているためです。そのぶん、このアカウントでパラメータを読める人は誰でも復号できます。復号できる人を絞りたいときは、カスタマー管理キーで暗号化し、そのキーへの `kms:Decrypt` を別に付けてください（[SecureString の復号を制限する](https://docs.aws.amazon.com/systems-manager/latest/userguide/ps-restrict-decryption.html)）。

## 中身

```
src/support_agent/tools.py        規程の検索・申請の照会・引き継ぎの3つの道具
src/support_agent/agent.py        エージェントの定義（システムプロンプトに今日の日付を入れる）
src/support_agent/main.py         コマンドラインから1回話しかける
src/support_agent/runtime.py      AgentCore Runtime で動かすときの入口
data/policies/                    社内規程（架空の会社、25本）
data/applications.json            申請のモックデータ
docs/corpus-design.md             規程の設計、罠の一覧、改定の演習の段取り
evals/rules.py                    コードで測れるもの
evals/jev_judge.py                Jev に採点させる
evals/run.py                      トレースを取る → 採点する → スコアを書き戻す
evals/dataset.jsonl               正解つきの質問集（26問）
evals/dataset.py                  質問集を読み、コードで測れるものを測る
evals/run_dataset.py              質問集をエージェントに流して採点する
tests/                            道具・取り込みスクリプト・採点の部品の単体テスト
scripts/deploy.sh                 cdkd でデプロイし、続けて規程を取り込む
scripts/ingest_policies.py        規程を KB に取り込み、終わるまで待つ
agentcore/agentcore.json          AgentCore CLI の設定
agentcore/cdk/lib/cdk-stack.ts    CDK のスタック（CLI が生成したものに、KB と権限を足した）
agentcore/cdk/lib/policy-knowledge-base.ts  規程の KB 一式
agentcore/cdk/lib/runtime-code.ts Runtime の zip に入れるものを絞る
```

## 覚え書き

- `aws login` で認証している場合、boto3 から使うのに `botocore[crt]` が要ります
- Langfuse からトレースを読むときは `fields` で取得する項目を指定します。既定では入出力が付いてこないので `core,basic,io` を指定します
- 採点は何度でも実行できますが、そのたびにスコアが積み上がります。`evals/run.py` は採点済みのトレースを飛ばします
- `agentcore/cdk` で `npx cdk` を実行すると、aws-cdk の CLI ではなく、このアプリ自身（`package.json` の bin の名前が `cdk`）が動きます。CDK の CLI を使うときは `./node_modules/.bin/cdk` を指定します
- S3 Vectors のインデックスでは、KB が本文を入れるメタデータ（`AMAZON_BEDROCK_TEXT` と `AMAZON_BEDROCK_METADATA`）を絞り込みに使わないキーにしています。絞り込みに使えるメタデータは1ベクトルあたり 2KB までで、作ったあとは変えられないためです

## ライセンス

MIT
