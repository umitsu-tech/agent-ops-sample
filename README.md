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

## 中身

```
src/support_agent/tools.py   FAQ 検索・注文照会・引き継ぎの3つの道具
src/support_agent/agent.py   エージェントの定義
src/support_agent/main.py    コマンドラインから1回話しかける
evals/rules.py               コードで測れるもの
evals/jev_judge.py           Jev に採点させる
evals/run.py                 トレースを取る → 採点する → スコアを書き戻す
data/                        FAQ と注文のモックデータ
```

## 覚え書き

- `aws login` で認証している場合、boto3 から使うのに `botocore[crt]` が要ります
- Langfuse からトレースを読むときは `fields` で取得する項目を指定します。既定では入出力が付いてこないので `core,basic,io` を指定します

## ライセンス

MIT
