# agent-ops-sample

技術同人誌の「AI エージェントの運用・評価」の章で使うサンプル。問い合わせ対応エージェントを作り、その振る舞いを観測して採点するところまでを扱う。

## 構成

- エージェント: Strands Agents + Amazon Bedrock（Nova 2 Lite）
- 観測: Langfuse Cloud（Hobby、日本リージョン）
- 実行環境: ローカル（`main.py`）と Amazon Bedrock AgentCore Runtime（`runtime.py`、AgentCore CLI `@aws/agentcore` で CodeZip デプロイ）
- 採点: TypeSafe AI の Jev

道具は3つ。FAQ 検索（search_faq）、注文照会（get_order_status）、担当部署への引き継ぎ（escalate）。中身はすべて `data/` のモックデータを引くだけ。

## 決めていること

- 読者が手元で動かせることを優先する。外部サービスへの接続はモックで済ませる
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

## 現在の状況

最終更新: 2026-09-23

- 完了: エージェント本体と道具3つ、Langfuse への送信、Jev での採点、結果のローカル保存、採点済みの重複防止
- 実際に採点できた例: 根拠 0.98 / 0.94（道具の結果どおり）、0.28 / 0.31（道具が返していないことを答えている）
- 進行中: AgentCore Runtime から Langfuse に送る準備（Issue #1、PR #2）。入口 `runtime.py`、`agentcore/` の設定、鍵を SSM から読む仕組み、README の手順まで作った。手元での起動（`.env` から読む経路）と Langfuse への到達は確認済み。SSM のパラメータはまだ作っておらず、デプロイもしていない
- 次の一歩: README の手順で SSM のパラメータを作り、`agentcore deploy --diff` で差分を確かめてからデプロイする。Runtime 経由のトレースが Langfuse に届くか確かめる
- その次: 評価の観点を見直す。いまの2観点（根拠・道具の選択）を、標準的な枠組みに合わせて「道具の選択」と「引数」に分け、セッション単位の用件達成と回答の関連性を足す
- 未着手: 本文の執筆

- 2026-09-23: AgentCore Runtime に実際にデプロイして、Langfuse の日本リージョンに1トレース（invoke_agent / execute_event_loop_cycle / chat / get_order_status など6件）が届くことを確認した。鍵は SSM Parameter Store から起動時に読めていた。確認後、スタック・SSM パラメータ・ロググループは削除済み
- このサンプル（通販の問い合わせ対応）は配管の検証用。本の題材は社内規程の Q&A エージェント（Knowledge Bases + S3 Vectors）で作り直す予定

## 運用ルール

- 作業の区切りごとに「現在の状況」を更新してコミットする
- 秘密情報はコミットしない。鍵は `.env`（gitignore 済み）に置く
