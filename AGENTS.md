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
- 規程の取り込み（StartIngestionJob）はスタックに入れず、デプロイのあとに `scripts/ingest_policies.py` でかける（`scripts/deploy.sh` が続けて呼ぶ）。取り込み中に次の取り込みを始めると `ConflictException` になるので、走っているものの完了を待ってから始め、終わるまで待つ。デプロイの途中で待つには自前の Lambda が要り、ランタイムの更新を抱えたくないのでスクリプトにした
- strands-agents-tools の `retrieve` は非推奨なので使わない。自前の道具で、チャンクの本文・スコア・出典を道具の結果に載せ、トレースに残す（根拠の採点に使う）
- Runtime の zip には `pyproject.toml`、`src/`、`data/applications.json` だけを入れる（`agentcore/cdk/lib/runtime-code.ts`）
- システムプロンプトに今日の日付（JST）を入れる。規程に施行日があるため
- 評価は Jev を主役にする。LLM-as-a-judge は他書でも扱われているため、そこでの困りごとを示す前振りとして短く扱う
- 正解つきの質問集（`evals/dataset.jsonl`）は罠の表と1対1にする。正しさは Jev、道具・引き継ぎ・出典はコードで測る。質問した日は 2026-09-24 に固定する（施行日で答えが変わる質問があるため）
- 採点結果は `results/` に残す。Langfuse の無料枠は保持30日で、原稿を書いている間に元データが消えるため
- `results/` はリポジトリに含めない

## 詰まった点

- `aws login` で認証している場合、boto3 から使うのに `botocore[crt]` が要る
- Langfuse は 2026-09-16 以降に作られた組織だと旧 API が 410 になる。observations API v2 を使う
- observations API は `fields="core,basic,io"` を指定しないと、道具の名前か入出力のどちらかが欠ける
- 同じトレースを何度採点してもスコアが積み上がるので、付いているスコアは飛ばし、付いていない観点だけを書き戻す（観点を足したときに、前に採点したトレースへ足りない分だけ付けるため）
- AgentCore Runtime は既定で ADOT を仕込むので、そのままだとトレースが Langfuse に届かない。`agentcore.json` の `instrumentation.enableOtel: false` と `DISABLE_ADOT_OBSERVABILITY=true` の両方で切る
- `agentcore.json` の `envVars` は平文でコミットされ、テンプレートにも残る。Langfuse の鍵は SSM Parameter Store の SecureString（`/agent-ops-sample/langfuse`、JSON 1つ）に置き、`runtime.py` が起動時に読む。ロールに足すのはそのパラメータの `ssm:GetParameter` だけ（`aws/ssm` キーなので `kms:Decrypt` は不要）
- AWS CLI の `file://` はパイプや標準入力を読めない（2.35.14 で確認）。鍵を `put-parameter` に渡すときは、0600 の一時ファイルを経由する
- `agentcore dev` は条件によって起動前にデプロイを走らせる。手元で試すときは `runtime.py` を直接起動する
- AgentCore CLI 0.22.0（`@aws/agentcore-cdk` 0.1.0-alpha.53）の CodeZip は `.env` を除かない。除くのは `.git`、`.venv`、`__pycache__`、`.pytest_cache`、`.DS_Store`、`node_modules` と `agentcore/` だけ。コードの置き場をリポジトリ直下にしていると、鍵の入った `.env` や `results/` が zip に入り、アセット用バケットと Runtime に載る。`runtime-code.ts` で、入れるものを列挙して別フォルダに写す形にした
- `agentcore/cdk` で `npx cdk` を実行すると、aws-cdk の CLI ではなくアプリ自身が動く（`package.json` の bin 名が `cdk`）。CDK の CLI は `./node_modules/.bin/cdk` で呼ぶ
- cdkd は `agentcore/cdk` をカレントにして動かす。`aws-targets.json` は自分で書く（`agentcore deploy` だけが自動で書く）。`cdkd diff` はステートバケットが要るので `cdkd bootstrap` の後。cdkd で作った Runtime は `agentcore invoke` / `status` から見えない（CLI は `.cli/deployed-state.json` を見る）ので、AWS CLI で呼ぶ
- S3 Vectors の VectorBucket は、cdkd では名前が必須（CloudFormation のような自動命名が無い）。`Ref` もバケット名を返すので、ARN は `attrVectorBucketArn` で取る
- cdkd（0.290.49）は、KB を作るときの `Bedrock Knowledge Base was unable to assume the given role` を IAM の反映待ちとして再試行しない（覚えている文言は `is unable to assume the role` など）。ロールを作った数秒後に KB を作ると1回で失敗し、スタックごとロールバックされる。KB のロール → 規程のアップロード（BucketDeployment）→ KB の順に作る依存を足して避けた（Issue #7、PR #8。時間で稼ぐやり方で保証ではない）
- BucketDeployment の Lambda のロググループは、CDK の既定では Retain（731日）。スタックと一緒に消えるよう Aspect で DESTROY にしている

## 現在の状況

最終更新: 2026-09-23

- 完了（Issue #1、PR #2。2026-09-23 にマージ済み）: AgentCore Runtime から Langfuse に送る配管。2026-09-23 に実機で1トレース届くことを確認し、スタック・SSM パラメータ・ロググループは削除済み
- 完了（Issue #3、PR #4。2026-09-23 にマージ済み。Codex の指摘2件には、取り込みをスタックから外してスクリプトにする形で対応した）: 題材を社内規程の Q&A エージェントに作り直した
  - 済み: 規程25本と罠の設計（`docs/corpus-design.md`）、道具3つ、システムプロンプト、KB（S3 Vectors）一式の CDK、取り込みとデプロイのスクリプト、Runtime の zip から `.env` を外す対応、evals の最小限の手直し、README
  - 手元で確かめたこと: 道具と取り込みスクリプトの単体テスト（botocore の Stubber）、CDK のテスト、`cdkd synth`（20リソース、テンプレートとアセットに鍵が入っていないこと）、`agentcore validate`、検索を手元の簡易検索に差し替えた Nova 2 Lite での会話（罠 1 と 5 で実際に誤答が出た）
- 対応済み: 2026-09-23 に `agentcore deploy` した Runtime の zip に `.env` が入っていた。CDK のアセット用バケット（`cdk-hnb659fds-assets-<アカウントID>-us-east-1`）の zip（1バージョンのみ）、手元の一時 zip、`agentcore/cdk/cdk.out` の写しは削除した。鍵は入れ替えない（個人アカウントの非公開バケットに約40分置かれていただけのため）
- デプロイ済み（2026-09-23、cdkd、us-east-1）: `cdkd bootstrap` のあと `scripts/deploy.sh` で20リソースを作り、規程25本の取り込みが COMPLETE。Runtime に1問投げて答えが返り、Langfuse にトレース（6件）が届いた。SSM パラメータ `/agent-ops-sample/langfuse` も置き直した。片付けは README の「片付ける」
- 完了（Issue #5、PR #9。2026-09-23 にマージ済み）: 罠の表を規程と突き合わせて直し（規程4本、質問と答え7件）、正解つきの質問集と採点（`evals/run_dataset.py`）を作った。デプロイした KB で2回流し、2回目（今の質問集）は正しさ 20/26、道具 25/26、道具の引数 22/26、引き継ぎ 19/25、出典 3/25、用件の達成 22/26、根拠の平均 0.72。回によって合否が入れ替わる問いがある（`docs/corpus-design.md` の「質問集での採点」）
- 完了（Issue #6、PR #10。2026-09-23 にマージ済み）: `evals/run.py` の観点に、道具の引数（correct / wrong / no_tool）と用件の達成（noul と choice）を足し、道具の選択と分けた。付いていないスコアだけを書き戻す。トレースから道具の引数も取る
- 次の一歩: 引き継ぎと出典が守られていないので、システムプロンプトを直す前後を質問集で比べる（4.2 の題材）
- その次: 改定の演習（`docs/corpus-design.md`）。答えの関連性はまだ測っていない
- 未着手: 本文の執筆

## 運用ルール

- 作業の区切りごとに「現在の状況」を更新してコミットする
- 秘密情報はコミットしない。鍵は `.env`（gitignore 済み）に置く
