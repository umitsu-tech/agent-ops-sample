# agent-ops-sample

技術同人誌の「AI エージェントの運用・評価」の章で使うサンプル。架空の会社の社内規程に答える Q&A エージェントを作り、その振る舞いを観測して採点するところまでを扱う。

## 構成

- エージェント: Strands Agents + Amazon Bedrock（Nova 2 Lite）
- 規程の検索: Amazon Bedrock Knowledge Bases（ベクトルストアは Amazon S3 Vectors、埋め込みは Titan Text Embeddings V2）
- 観測: Langfuse Cloud（Hobby、日本リージョン）
- 実行環境: ローカル（`main.py`）と Amazon Bedrock AgentCore Runtime（`runtime.py`、CodeZip）
- インフラ: AgentCore CLI `@aws/agentcore` が生成した CDK アプリ（`agentcore/cdk`）。cdkd か `agentcore deploy` でデプロイする
- 採点: TypeSafe AI の Jev

道具は3つ。規程の検索（search_policies、KB の Retrieve を自前で呼ぶ）、申請の照会（get_application_status、`data/applications.json` のモック）、人事・総務・経理への引き継ぎ（escalate、モック）。規程は `data/policies/` の25本で、罠の一覧と改定の演習の段取りは `docs/corpus-design.md` にある。

## 決めていること

- 読者が手元で動かせることを優先する。規程の検索だけは Knowledge Bases を使い（検索の取りこぼしや紛らわしい文書を題材にするため）、申請の照会と引き継ぎはモックで済ませる
- 待機中の固定費が出ない構成にする。ベクトルストアは S3 Vectors
- KB は CDK に L1 で書く（`agentcore/cdk/lib/policy-knowledge-base.ts`）。AgentCore CLI 0.22.0 の `knowledgeBases` は MANAGED 型で、ベクトルストアを選べないため使わない
- strands-agents-tools の `retrieve` は非推奨なので使わない。自前の道具で、チャンクの本文・スコア・出典を道具の結果に載せ、トレースに残す（根拠の採点に使う）
- Runtime の zip には `pyproject.toml`、`src/`、`data/applications.json` だけを入れる（`agentcore/cdk/lib/runtime-code.ts`）
- システムプロンプトに今日の日付（JST）を入れる。規程に施行日があるため
- 評価は Jev を主役にする。LLM-as-a-judge は他書でも扱われているため、そこでの困りごとを示す前振りとして短く扱う
- 採点結果は `results/` に残す。Langfuse の無料枠は保持30日で、原稿を書いている間に元データが消えるため
- `results/` はリポジトリに含めない

## 詰まった点

- `aws login` で認証している場合、boto3 から使うのに `botocore[crt]` が要る
- Langfuse は 2026-09-16 以降に作られた組織だと旧 API が 410 になる。observations API v2 を使う
- observations API は `fields="core,basic,io"` を指定しないと、道具の名前か入出力のどちらかが欠ける
- 同じトレースを何度採点してもスコアが積み上がるので、採点済みは飛ばす
- AgentCore Runtime は既定で ADOT を仕込むので、そのままだとトレースが Langfuse に届かない。`agentcore.json` の `instrumentation.enableOtel: false` と `DISABLE_ADOT_OBSERVABILITY=true` の両方で切る
- `agentcore.json` の `envVars` は平文でコミットされ、テンプレートにも残る。Langfuse の鍵は SSM Parameter Store の SecureString（`/agent-ops-sample/langfuse`、JSON 1つ）に置き、`runtime.py` が起動時に読む。ロールに足すのはそのパラメータの `ssm:GetParameter` だけ（`aws/ssm` キーなので `kms:Decrypt` は不要）
- AWS CLI の `file://` はパイプや標準入力を読めない（2.35.14 で確認）。鍵を `put-parameter` に渡すときは、0600 の一時ファイルを経由する
- `agentcore dev` は条件によって起動前にデプロイを走らせる。手元で試すときは `runtime.py` を直接起動する
- AgentCore CLI 0.22.0（`@aws/agentcore-cdk` 0.1.0-alpha.53）の CodeZip は `.env` を除かない。除くのは `.git`、`.venv`、`__pycache__`、`.pytest_cache`、`.DS_Store`、`node_modules` と `agentcore/` だけ。コードの置き場をリポジトリ直下にしていると、鍵の入った `.env` や `results/` が zip に入り、アセット用バケットと Runtime に載る。`runtime-code.ts` で、入れるものを列挙して別フォルダに写す形にした
- `agentcore/cdk` で `npx cdk` を実行すると、aws-cdk の CLI ではなくアプリ自身が動く（`package.json` の bin 名が `cdk`）。CDK の CLI は `./node_modules/.bin/cdk` で呼ぶ
- cdkd は `agentcore/cdk` をカレントにして動かす。`aws-targets.json` は自分で書く（`agentcore deploy` だけが自動で書く）。`cdkd diff` はステートバケットが要るので `cdkd bootstrap` の後。cdkd で作った Runtime は `agentcore invoke` / `status` から見えない（CLI は `.cli/deployed-state.json` を見る）ので、AWS CLI で呼ぶ
- S3 Vectors の VectorBucket は、cdkd では名前が必須（CloudFormation のような自動命名が無い）。`Ref` もバケット名を返すので、ARN は `attrVectorBucketArn` で取る
- BucketDeployment と AwsCustomResource の Lambda のロググループは、CDK の既定では Retain（731日）。スタックと一緒に消えるよう Aspect で DESTROY にしている

## 現在の状況

最終更新: 2026-09-23

- 完了（Issue #1、PR #2。未マージ）: AgentCore Runtime から Langfuse に送る配管。2026-09-23 に実機で1トレース届くことを確認し、スタック・SSM パラメータ・ロググループは削除済み
- 進行中（Issue #3、PR #2 に積んだ PR。未マージ）: 題材を社内規程の Q&A エージェントに作り直した
  - 済み: 規程25本と罠の設計（`docs/corpus-design.md`）、道具3つ、システムプロンプト、KB（S3 Vectors）一式と取り込み開始の CDK、Runtime の zip から `.env` を外す対応、evals の最小限の手直し、README
  - 手元で確かめたこと: 道具の単体テスト（botocore の Stubber）、CDK のテスト、`cdk synth` と `cdkd synth`（25リソース、テンプレートと zip に鍵が入っていないこと）、`agentcore validate`、検索を手元の簡易検索に差し替えた Nova 2 Lite での会話（罠 1 と 5 で実際に誤答が出た）
  - まだ: AWS へのデプロイ（`cdkd bootstrap` / `cdkd deploy`）、本物の KB での検索と取り込み、Runtime の呼び出し、新しいトレースでの採点
- 要対応: 2026-09-23 に `agentcore deploy` した Runtime の zip（`.env` 入り）が、CDK のアセット用バケット（`cdk-hnb659fds-assets-<アカウントID>-us-east-1`、バージョニング有効）に残っている。オブジェクトの全バージョンを消し、Langfuse と TypeSafe の鍵を入れ替える
- 次の一歩: PR #2 と Issue #3 の PR をマージするか判断する。そのあと README の手順で `cdkd bootstrap` → `cdkd diff` → `cdkd deploy` → 取り込みの確認 → 呼び出し → Langfuse で確認 → 採点
- その次: 罠の表を正解つきの質問集にして、正しさ・引き継ぎ・出典の採点を足す。評価の観点（道具の選択と引数を分ける、セッション単位の用件達成、回答の関連性）も見直す。改定の演習（`docs/corpus-design.md`）を行う
- 未着手: 本文の執筆

## 運用ルール

- 作業の区切りごとに「現在の状況」を更新してコミットする
- 秘密情報はコミットしない。鍵は `.env`（gitignore 済み）に置く
