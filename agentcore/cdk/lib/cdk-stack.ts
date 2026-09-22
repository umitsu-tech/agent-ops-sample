import {
  AgentCoreApplication,
  AgentCoreMcp,
  AgentCorePaymentManager,
  AgentCorePaymentConnector,
  type AgentCoreProjectSpec,
  type AgentCoreMcpSpec,
  type CustomJWTAuthorizerConfig,
  type HarnessDeploymentConfig,
} from '@aws/agentcore-cdk';
import { ArnFormat, Aspects, CfnOutput, RemovalPolicy, Stack, type StackProps } from 'aws-cdk-lib';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as logs from 'aws-cdk-lib/aws-logs';
import { Construct } from 'constructs';
import * as path from 'path';
import { PolicyKnowledgeBase } from './policy-knowledge-base';
import { stageRuntimeCode } from './runtime-code';

/**
 * Langfuse の鍵を SSM Parameter Store から読む Runtime の名前（agentcore.json の runtimes[].name）。
 */
const LANGFUSE_RUNTIME_NAME = 'SupportAgent';

/**
 * 鍵を置く SSM パラメータの名前を渡す環境変数（agentcore.json の envVars に書く）。
 */
const LANGFUSE_SSM_PARAMETER_ENV = 'LANGFUSE_SSM_PARAMETER';

/**
 * 社内規程の Knowledge Base を検索する Runtime の名前（agentcore.json の runtimes[].name）。
 */
const POLICY_RUNTIME_NAME = 'SupportAgent';

/**
 * 規程の Markdown を置いたフォルダ。CLI も cdkd も、このアプリを agentcore/cdk をカレントにして動かす
 * （bin/cdk.ts も同じ前提で ../agentcore.json を読んでいる）。
 */
const REPO_ROOT = path.resolve(process.cwd(), '..', '..');
const POLICY_DOCS_DIR = path.join(REPO_ROOT, 'data', 'policies');

/**
 * Runtime の zip の元にするフォルダの置き場（agentcore/.gitignore で .cache/* を除外済み）。
 */
const RUNTIME_CODE_CACHE_DIR = path.resolve(process.cwd(), '..', '.cache', 'runtime-code');

/**
 * Harness deployment config: role-scoped fields (for IAM role + container build)
 * plus the full validated spec + its config directory so the L3 construct can
 * synthesize the AWS::BedrockAgentCore::Harness resource.
 */
export type HarnessConfig = HarnessDeploymentConfig;

export interface PaymentConnectorSpec {
  name: string;
  provider: 'CoinbaseCDP' | 'StripePrivy';
  credentialProviderArn: string;
}

export interface PaymentSpec {
  name: string;
  description?: string;
  authorizerType: 'AWS_IAM' | 'CUSTOM_JWT';
  authorizerConfiguration?: { customJWTAuthorizer: CustomJWTAuthorizerConfig };
  autoPayment?: boolean;
  paymentToolAllowlist?: string[];
  networkPreferences?: string[];
  connectors: PaymentConnectorSpec[];
}

export interface AgentCoreStackProps extends StackProps {
  /**
   * The AgentCore project specification containing agents, memories, and credentials.
   */
  spec: AgentCoreProjectSpec;
  /**
   * The MCP specification containing gateways and servers.
   */
  mcpSpec?: AgentCoreMcpSpec;
  /**
   * Credential provider ARNs from deployed state, keyed by credential name.
   */
  credentials?: Record<string, { credentialProviderArn: string; clientSecretArn?: string }>;
  /**
   * Harness role configurations.
   */
  harnesses?: HarnessConfig[];
  /**
   * Parsed connectorParameters for non-S3 KB data sources, keyed by
   * connectorConfigFile path. Forwarded to AgentCoreApplication.
   */
  connectorParametersByFile?: Record<string, Record<string, unknown>>;
  /**
   * Payment specifications with resolved credential provider ARNs.
   */
  paymentSpec?: PaymentSpec[];
}

function toCdkId(name: string): string {
  return name.replace(/_/g, '');
}

/**
 * Decide whether a deployed runtime should receive payment env vars + IAM grants.
 * Payments today only ships a runtime shim for Python HTTP runtimes; injecting
 * AGENTCORE_PAYMENT_* env vars into TypeScript / MCP / A2A / AGUI runtimes
 * would surface env vars they cannot consume and would dilute least-privilege
 * IAM grants for runtimes that never call ProcessPayment.
 */
function isPaymentEligibleAgent(agent: { entrypoint?: string; protocol?: string }): boolean {
  if (agent.protocol && agent.protocol !== 'HTTP') {
    return false;
  }
  const entrypoint = typeof agent.entrypoint === 'string' ? agent.entrypoint : '';
  const entrypointFile = entrypoint.split(':')[0] ?? '';
  return entrypointFile.endsWith('.py');
}

/**
 * CDK Stack that deploys AgentCore infrastructure.
 *
 * This is a thin wrapper that instantiates L3 constructs.
 * All resource logic and outputs are contained within the L3 constructs.
 */
export class AgentCoreStack extends Stack {
  /** The AgentCore application containing all agent environments */
  public readonly application: AgentCoreApplication;

  constructor(scope: Construct, id: string, props: AgentCoreStackProps) {
    super(scope, id, props);

    const { spec, mcpSpec, credentials, harnesses, connectorParametersByFile, paymentSpec } = props;

    // Runtime の zip に入れるものを絞る（agent-ops-sample で足した処理。理由は runtime-code.ts）。
    // リポジトリ直下を指している codeLocation を、必要なものだけを写したフォルダに差し替える。
    const stagedSpec: AgentCoreProjectSpec = {
      ...spec,
      runtimes: spec.runtimes.map(runtime =>
        runtime.name === POLICY_RUNTIME_NAME && runtime.build === 'CodeZip'
          ? {
              ...runtime,
              // スキーマ上は検証済みのパスを表す型（DirectoryPath）なので、写し先の文字列を合わせる
              codeLocation: stageRuntimeCode(
                REPO_ROOT,
                path.join(RUNTIME_CODE_CACHE_DIR, runtime.name)
              ) as typeof runtime.codeLocation,
            }
          : runtime
      ),
    };

    // Create AgentCoreApplication with all agents and harness roles
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const appProps: Record<string, unknown> = { spec: stagedSpec };
    if (harnesses?.length) {
      appProps.harnesses = harnesses;
    }
    if (connectorParametersByFile && Object.keys(connectorParametersByFile).length > 0) {
      appProps.connectorParametersByFile = connectorParametersByFile;
    }
    if (credentials) {
      appProps.credentials = credentials;
    }
    this.application = new AgentCoreApplication(this, 'Application', appProps as any);

    // Runtime が SSM Parameter Store から Langfuse の鍵を読めるようにする（agent-ops-sample で足した処理）。
    // 鍵そのものはテンプレートに入れず、実行時に runtime.py が読む。ここで渡すのは読む権限だけ。
    // パラメータ名は agentcore.json の envVars から取り、権限はそのパラメータ1つに絞る。
    // aws/ssm（AWS 管理キー）で暗号化する前提なので kms:Decrypt は足さない。このキーは同じアカウントの
    // プリンシパルに Parameter Store 経由の復号を許可している。
    const langfuseRuntime = this.application.environments.get(LANGFUSE_RUNTIME_NAME);
    const parameterName = langfuseRuntime?.agent.envVars?.find(v => v.name === LANGFUSE_SSM_PARAMETER_ENV)?.value;
    if (langfuseRuntime && parameterName) {
      langfuseRuntime.runtime.addToPolicy(
        new iam.PolicyStatement({
          actions: ['ssm:GetParameter'],
          resources: [
            this.formatArn({
              service: 'ssm',
              resource: 'parameter',
              // ARN では先頭の / を重ねない（/agent-ops-sample/langfuse → parameter/agent-ops-sample/langfuse）
              resourceName: parameterName.replace(/^\/+/, ''),
              arnFormat: ArnFormat.SLASH_RESOURCE_NAME,
            }),
          ],
        })
      );
    }

    // 社内規程の Knowledge Base（S3 Vectors）を作り、Runtime から検索できるようにする（agent-ops-sample で足した処理）。
    // Runtime に渡すのは KB の ID（秘密ではない）と、その KB 1つに対する bedrock:Retrieve だけ。
    const policyKnowledgeBase = new PolicyKnowledgeBase(this, 'PolicyKnowledgeBase', {
      sourceDir: POLICY_DOCS_DIR,
      namePrefix: this.stackName,
    });
    const policyRuntime = this.application.environments.get(POLICY_RUNTIME_NAME);
    if (policyRuntime) {
      policyRuntime.runtime.addEnvironmentVariable('KNOWLEDGE_BASE_ID', policyKnowledgeBase.knowledgeBaseId);
      policyRuntime.runtime.addToPolicy(
        new iam.PolicyStatement({
          actions: ['bedrock:Retrieve'],
          resources: [policyKnowledgeBase.knowledgeBaseArn],
        })
      );
    }
    // アップロードと取り込み開始の Lambda が使うロググループは、CDK の既定では保持期間 731 日で
    // スタックを消しても残る（Retain）。サンプルなので、スタックと一緒に消えるようにしておく
    Aspects.of(this).add({
      visit(node) {
        if (node instanceof logs.CfnLogGroup) {
          node.applyRemovalPolicy(RemovalPolicy.DESTROY);
        }
      },
    });
    // 手元の main.py から同じ KB を使うときや、取り込みの状況を確かめるときに要る値を出しておく
    new CfnOutput(this, 'PolicyKnowledgeBaseId', { value: policyKnowledgeBase.knowledgeBaseId });
    new CfnOutput(this, 'PolicyDataSourceId', { value: policyKnowledgeBase.dataSourceId });
    new CfnOutput(this, 'PolicyDocsBucketName', { value: policyKnowledgeBase.docsBucket.bucketName });
    new CfnOutput(this, 'PolicyVectorBucketName', { value: policyKnowledgeBase.vectorBucketName });
    new CfnOutput(this, 'PolicyIndexName', { value: policyKnowledgeBase.indexName });

    // Create AgentCoreMcp if there are gateways configured
    if (mcpSpec?.agentCoreGateways && mcpSpec.agentCoreGateways.length > 0) {
      new AgentCoreMcp(this, 'Mcp', {
        projectName: spec.name,
        mcpSpec,
        agentCoreApplication: this.application,
        credentials,
        projectTags: spec.tags,
      });
    }

    // Create payment infrastructure via CFN constructs
    if (paymentSpec && paymentSpec.length > 0) {
      for (const payment of paymentSpec) {
        const mgrId = toCdkId(payment.name);
        const manager = new AgentCorePaymentManager(this, `Payment${mgrId}`, {
          projectName: spec.name,
          name: payment.name,
          authorizerType: payment.authorizerType,
          description: payment.description,
          authorizerConfiguration: payment.authorizerConfiguration,
          tags: spec.tags,
        });

        const prefix = `AGENTCORE_PAYMENT_${payment.name.toUpperCase().replace(/-/g, '_')}`;

        // Wire env vars from construct output tokens into eligible agent environments only.
        // See isPaymentEligibleAgent — non-Python or non-HTTP runtimes have no shim that
        // can consume these env vars, and giving them sts:AssumeRole on the
        // ProcessPaymentRole would broaden the privilege surface unnecessarily.
        for (const env of this.application.environments.values()) {
          if (!isPaymentEligibleAgent(env.agent)) {
            continue;
          }
          env.runtime.addEnvironmentVariable(`${prefix}_MANAGER_ARN`, manager.paymentManagerArn);
          env.runtime.addEnvironmentVariable(`${prefix}_PROCESS_PAYMENT_ROLE_ARN`, manager.processPaymentRoleArn);

          // Grant runtime execution role permission to assume the ProcessPaymentRole.
          // The ProcessPaymentRole's trust policy allows AccountRootPrincipal, but the
          // caller still needs sts:AssumeRole on its own role to perform the assumption.
          env.runtime.role.addToPrincipalPolicy(
            new iam.PolicyStatement({
              actions: ['sts:AssumeRole'],
              resources: [manager.processPaymentRoleArn],
            })
          );

          // Grant payment data-plane actions directly to the runtime role.
          //
          // NOTE: This deviates from the canonical role model in the AgentCore Payments
          // beta guide, which assigns Get/List/Create instrument+session actions to a
          // separate ManagementRole and limits the agent's role to ProcessPayment only.
          // The current SDK plugin (AgentCorePaymentsPlugin.generate_payment_header)
          // calls GetPaymentInstrument internally during the 402 auto-pay path, so the
          // runtime role needs read access. CreatePaymentSession is included so
          // `agentcore invoke --auto-session` works without a separate ManagementRole
          // call. Tighten this if the SDK is updated to accept pre-fetched instrument
          // details and split create-session into a backend-only flow.
          env.runtime.role.addToPrincipalPolicy(
            new iam.PolicyStatement({
              actions: [
                'bedrock-agentcore:GetPaymentInstrument',
                'bedrock-agentcore:ListPaymentInstruments',
                'bedrock-agentcore:GetPaymentInstrumentBalance',
                'bedrock-agentcore:GetPaymentSession',
                'bedrock-agentcore:ListPaymentSessions',
                'bedrock-agentcore:CreatePaymentSession',
                'bedrock-agentcore:ProcessPayment',
              ],
              resources: [manager.paymentManagerArn, `${manager.paymentManagerArn}/*`],
            })
          );

          if (payment.autoPayment !== undefined) {
            env.runtime.addEnvironmentVariable(`${prefix}_AUTO_PAYMENT`, String(payment.autoPayment));
          }
          if (payment.paymentToolAllowlist) {
            env.runtime.addEnvironmentVariable(`${prefix}_TOOL_ALLOWLIST`, payment.paymentToolAllowlist.join(','));
          }
          if (payment.networkPreferences) {
            env.runtime.addEnvironmentVariable(`${prefix}_NETWORK_PREFERENCES`, payment.networkPreferences.join(','));
          }
          if (payment.authorizerType === 'CUSTOM_JWT') {
            env.runtime.addEnvironmentVariable(`${prefix}_AUTH_MODE`, 'bearer');
          }
        }

        // Create connectors for this manager
        for (const connector of payment.connectors) {
          const connId = toCdkId(connector.name);
          const conn = new AgentCorePaymentConnector(this, `Payment${mgrId}${connId}`, {
            projectName: spec.name,
            paymentManager: manager,
            connectorName: connector.name,
            connectorType: connector.provider,
            credentialProviderArn: connector.credentialProviderArn,
          });

          // Wire first connector's ID as env var (eligible agents only)
          if (connector === payment.connectors[0]) {
            for (const env of this.application.environments.values()) {
              if (!isPaymentEligibleAgent(env.agent)) continue;
              env.runtime.addEnvironmentVariable(`${prefix}_CONNECTOR_ID`, conn.paymentConnectorId);
            }
          }

          new CfnOutput(this, `Payment${mgrId}${connId}ConnectorId`, {
            value: conn.paymentConnectorId,
          });
        }

        // CFN Outputs for post-deploy state parsing
        new CfnOutput(this, `Payment${mgrId}ManagerArn`, {
          value: manager.paymentManagerArn,
        });
        new CfnOutput(this, `Payment${mgrId}ManagerId`, {
          value: manager.paymentManagerId,
        });
        new CfnOutput(this, `Payment${mgrId}ProcessPaymentRoleArn`, {
          value: manager.processPaymentRoleArn,
        });
        new CfnOutput(this, `Payment${mgrId}ResourceRetrievalRoleArn`, {
          value: manager.resourceRetrievalRoleArn,
        });
      }
    }

    // Stack-level output
    new CfnOutput(this, 'StackNameOutput', {
      description: 'Name of the CloudFormation Stack',
      value: this.stackName,
    });
  }
}
