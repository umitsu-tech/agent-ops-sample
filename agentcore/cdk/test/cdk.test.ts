import * as cdk from 'aws-cdk-lib';
import { Match, Template } from 'aws-cdk-lib/assertions';
import { AgentCoreStack } from '../lib/cdk-stack';
import { stageRuntimeCode } from '../lib/runtime-code';
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';

test('AgentCoreStack synthesizes with empty spec', () => {
  const app = new cdk.App();
  const stack = new AgentCoreStack(app, 'TestStack', {
    spec: {
      name: 'testproject',
      version: 1,
      managedBy: 'CDK' as const,
      runtimes: [],
      memories: [],
      credentials: [],
      evaluators: [],
      onlineEvalConfigs: [],
      configBundles: [],
      policyEngines: [],
      payments: [],
      agentCoreGateways: [],
      mcpRuntimeTools: [],
      unassignedTargets: [],
      datasets: [],
      knowledgeBases: [],
    },
  });
  const template = Template.fromStack(stack);
  template.hasOutput('StackNameOutput', {
    Description: 'Name of the CloudFormation Stack',
  });
});

// ここから agent-ops-sample で足したテスト

const EMPTY_SPEC = {
  name: 'testproject',
  version: 1,
  managedBy: 'CDK' as const,
  runtimes: [],
  memories: [],
  credentials: [],
  evaluators: [],
  onlineEvalConfigs: [],
  configBundles: [],
  policyEngines: [],
  payments: [],
  agentCoreGateways: [],
  mcpRuntimeTools: [],
  unassignedTargets: [],
  datasets: [],
  knowledgeBases: [],
};

test('社内規程の Knowledge Base を S3 Vectors で作る', () => {
  const app = new cdk.App();
  const stack = new AgentCoreStack(app, 'TestStack', { spec: EMPTY_SPEC });
  const template = Template.fromStack(stack);

  template.hasResourceProperties('AWS::S3Vectors::Index', {
    Dimension: 1024,
    DistanceMetric: 'cosine',
    DataType: 'float32',
    MetadataConfiguration: { NonFilterableMetadataKeys: ['AMAZON_BEDROCK_TEXT', 'AMAZON_BEDROCK_METADATA'] },
  });
  template.hasResourceProperties('AWS::Bedrock::KnowledgeBase', {
    StorageConfiguration: { Type: 'S3_VECTORS' },
    KnowledgeBaseConfiguration: {
      VectorKnowledgeBaseConfiguration: {
        EmbeddingModelConfiguration: { BedrockEmbeddingModelConfiguration: { Dimensions: 1024 } },
      },
    },
  });
  template.hasResourceProperties('AWS::Bedrock::DataSource', {
    DataSourceConfiguration: { S3Configuration: { InclusionPrefixes: ['policies/'] } },
  });
  template.resourceCountIs('Custom::CDKBucketDeployment', 1);
  // KB はロールの反映を待つため、規程のアップロードのあとに作る
  template.hasResource('AWS::Bedrock::KnowledgeBase', {
    DependsOn: Match.arrayWith([Match.stringLikeRegexp('DeployDocsCustomResource')]),
  });
  // 取り込みはデプロイのあとに scripts/ingest_policies.py でかける。スタックでも始めると、スクリプトの取り込みとぶつかる
  template.resourceCountIs('Custom::AWS', 0);
  template.hasOutput('PolicyKnowledgeBaseId', {});
});

test('Runtime の zip の元には、挙げたものだけを写す（.env を入れない）', () => {
  const repo = fs.mkdtempSync(path.join(os.tmpdir(), 'repo-'));
  const dest = path.join(fs.mkdtempSync(path.join(os.tmpdir(), 'dest-')), 'SupportAgent');
  fs.writeFileSync(path.join(repo, '.env'), 'SECRET=dummy');
  fs.writeFileSync(path.join(repo, 'pyproject.toml'), '[project]');
  fs.mkdirSync(path.join(repo, 'src', 'pkg', '__pycache__'), { recursive: true });
  fs.writeFileSync(path.join(repo, 'src', 'pkg', 'a.py'), '');
  fs.writeFileSync(path.join(repo, 'src', 'pkg', '__pycache__', 'a.pyc'), '');
  fs.mkdirSync(path.join(repo, 'data', 'policies'), { recursive: true });
  fs.writeFileSync(path.join(repo, 'data', 'applications.json'), '{}');
  fs.writeFileSync(path.join(repo, 'data', 'policies', 'x.md'), '');
  fs.mkdirSync(path.join(repo, 'results'));

  stageRuntimeCode(repo, dest);

  expect(fs.existsSync(path.join(dest, '.env'))).toBe(false);
  expect(fs.existsSync(path.join(dest, 'results'))).toBe(false);
  expect(fs.existsSync(path.join(dest, 'data', 'policies'))).toBe(false);
  expect(fs.existsSync(path.join(dest, 'src', 'pkg', '__pycache__'))).toBe(false);
  expect(fs.existsSync(path.join(dest, 'src', 'pkg', 'a.py'))).toBe(true);
  expect(fs.existsSync(path.join(dest, 'data', 'applications.json'))).toBe(true);
});
