import * as fs from 'fs';
import * as path from 'path';

/**
 * Runtime の zip に入れるもの（リポジトリ直下からの相対パス）。agent-ops-sample で足した処理。
 *
 * AgentCore CLI 0.22.0（@aws/agentcore-cdk 0.1.0-alpha.53）の CodeZip は、codeLocation の中身を
 * .git / .venv / node_modules / agentcore などを除いてほぼそのまま固める。.env は除かれないので、
 * codeLocation をリポジトリ直下のままにすると、鍵の入った .env が zip に入り、アセット用の S3 バケットと
 * Runtime に載ってしまう。そこで、必要なものだけを別のフォルダに写し、そのフォルダを固めさせる。
 */
export const RUNTIME_CODE_INCLUDES = ['pyproject.toml', 'src', 'data/applications.json'];

/** 写すときに飛ばすもの（Python のキャッシュなど） */
const SKIP_NAMES = new Set(['__pycache__', '.DS_Store']);

/**
 * includes に挙げたものだけを destDir に写す。destDir は毎回作り直す。
 *
 * @param repoRoot リポジトリ直下
 * @param destDir 写し先（gitignore 済みの agentcore/.cache の下を使う）
 * @returns 写し先の絶対パス
 */
export function stageRuntimeCode(
  repoRoot: string,
  destDir: string,
  includes: string[] = RUNTIME_CODE_INCLUDES
): string {
  fs.rmSync(destDir, { recursive: true, force: true });
  for (const include of includes) {
    const from = path.join(repoRoot, include);
    if (!fs.existsSync(from)) {
      throw new Error(`Runtime に入れる ${include} が見つかりません（${from}）`);
    }
    fs.cpSync(from, path.join(destDir, include), {
      recursive: true,
      filter: source => !SKIP_NAMES.has(path.basename(source)),
    });
  }
  return destDir;
}
