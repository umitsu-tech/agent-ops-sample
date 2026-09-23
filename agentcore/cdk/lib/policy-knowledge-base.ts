import { RemovalPolicy, Stack } from 'aws-cdk-lib';
import * as bedrock from 'aws-cdk-lib/aws-bedrock';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as s3deploy from 'aws-cdk-lib/aws-s3-deployment';
import * as s3vectors from 'aws-cdk-lib/aws-s3vectors';
import { Construct } from 'constructs';

/**
 * 埋め込みモデル。Titan Text Embeddings V2 は 1024 次元で使う。
 * インデックスの次元数と KB の設定の次元数は、必ず同じ値にする（ずれると取り込みが失敗する）。
 */
const EMBEDDING_MODEL_ID = 'amazon.titan-embed-text-v2:0';
const EMBEDDING_DIMENSIONS = 1024;

/** 規程を置くバケットの中のフォルダ。データソースはこのフォルダだけを取り込む */
const DOCS_PREFIX = 'policies/';

export interface PolicyKnowledgeBaseProps {
  /** 規程の Markdown を置いた手元のフォルダ（絶対パス） */
  readonly sourceDir: string;
  /**
   * 名前の頭に付ける文字列。KB とベクトルバケットの名前に使う。
   * ベクトルバケットの名前は小文字・数字・ハイフンだけなので、小文字にして使う。
   */
  readonly namePrefix: string;
}

/**
 * 社内規程を検索する Knowledge Base 一式（agent-ops-sample で足した construct）。
 *
 * - 規程を置く S3 バケットと、data/policies のアップロード
 * - ベクトルの置き場（S3 Vectors のベクトルバケットとインデックス）
 * - KB のサービスロール、KB、データソース
 *
 * 取り込み（StartIngestionJob）はスタックに入れず、デプロイのあとに scripts/ingest_policies.py でかける。
 * デプロイの途中で取り込みの完了を待つには自前の Lambda が要るため、スクリプトにしている。
 *
 * AgentCore CLI の agentcore.json にも knowledgeBases はあるが、0.22.0 が作る KB は MANAGED 型で、
 * ベクトルストアを選べない。S3 Vectors を使うため、ここでは L1 で組み立てる。
 */
export class PolicyKnowledgeBase extends Construct {
  public readonly knowledgeBaseId: string;
  public readonly knowledgeBaseArn: string;
  public readonly dataSourceId: string;
  public readonly docsBucket: s3.Bucket;
  public readonly vectorBucketName: string;
  public readonly indexName: string;

  constructor(scope: Construct, id: string, props: PolicyKnowledgeBaseProps) {
    super(scope, id);

    const stack = Stack.of(this);
    const { partition, region, account } = stack;
    const embeddingModelArn = `arn:${partition}:bedrock:${region}::foundation-model/${EMBEDDING_MODEL_ID}`;

    // 規程の原本を置くバケット。スタックを消すときに中身ごと消す（サンプルなので残さない）
    this.docsBucket = new s3.Bucket(this, 'DocsBucket', {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      removalPolicy: RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
    });

    // data/policies をバケットの policies/ にそのまま置く。消したファイルはバケットからも消す（prune）
    const deployment = new s3deploy.BucketDeployment(this, 'DeployDocs', {
      sources: [s3deploy.Source.asset(props.sourceDir)],
      destinationBucket: this.docsBucket,
      destinationKeyPrefix: DOCS_PREFIX,
      prune: true,
    });

    // ベクトルの置き場。OpenSearch Serverless と違い、待機中の固定費がかからない
    this.vectorBucketName = `${props.namePrefix.toLowerCase()}-policies`;
    this.indexName = 'policies';
    const vectorBucket = new s3vectors.CfnVectorBucket(this, 'VectorBucket', {
      vectorBucketName: this.vectorBucketName,
    });
    const index = new s3vectors.CfnIndex(this, 'Index', {
      vectorBucketArn: vectorBucket.attrVectorBucketArn,
      indexName: this.indexName,
      dataType: 'float32',
      dimension: EMBEDDING_DIMENSIONS,
      distanceMetric: 'cosine',
      // KB はチャンクの本文とメタデータをベクトルのメタデータに入れる。絞り込みに使えるメタデータは
      // 1 ベクトルあたり 2KB までなので、本文などは絞り込みに使わないキーにしておく（作った後は変えられない）
      metadataConfiguration: {
        nonFilterableMetadataKeys: ['AMAZON_BEDROCK_TEXT', 'AMAZON_BEDROCK_METADATA'],
      },
    });

    // KB が使うロール。Bedrock から、このアカウントの KB としてだけ引き受けられるようにする
    const role = new iam.Role(this, 'KnowledgeBaseRole', {
      assumedBy: new iam.ServicePrincipal('bedrock.amazonaws.com', {
        conditions: {
          StringEquals: { 'aws:SourceAccount': account },
          ArnLike: { 'aws:SourceArn': `arn:${partition}:bedrock:${region}:${account}:knowledge-base/*` },
        },
      }),
      description: 'Knowledge Base role for the policy Q&A sample',
    });
    role.addToPolicy(
      new iam.PolicyStatement({
        actions: ['bedrock:InvokeModel'],
        resources: [embeddingModelArn],
      })
    );
    role.addToPolicy(
      new iam.PolicyStatement({
        actions: [
          's3vectors:PutVectors',
          's3vectors:GetVectors',
          's3vectors:DeleteVectors',
          's3vectors:QueryVectors',
          's3vectors:GetIndex',
        ],
        resources: [index.attrIndexArn],
      })
    );
    this.docsBucket.grantRead(role, `${DOCS_PREFIX}*`);

    const knowledgeBase = new bedrock.CfnKnowledgeBase(this, 'KnowledgeBase', {
      name: `${props.namePrefix}-policies`,
      description: 'Company policy documents for the policy Q&A sample',
      roleArn: role.roleArn,
      knowledgeBaseConfiguration: {
        type: 'VECTOR',
        vectorKnowledgeBaseConfiguration: {
          embeddingModelArn,
          embeddingModelConfiguration: {
            bedrockEmbeddingModelConfiguration: {
              dimensions: EMBEDDING_DIMENSIONS,
              embeddingDataType: 'FLOAT32',
            },
          },
        },
      },
      storageConfiguration: {
        type: 'S3_VECTORS',
        s3VectorsConfiguration: {
          indexArn: index.attrIndexArn,
        },
      },
    });
    // KB を作るときに埋め込みモデルとインデックスへの権限が確かめられる。ロールのポリシーは
    // 別リソースとして並行に作られるため、ポリシーが付いてから KB を作るよう順番を決めておく
    knowledgeBase.node.addDependency(role);
    // ロールを作った直後に KB を作ると、ロールがまだ反映されておらず「was unable to assume the given role」で失敗する。
    // cdkd（0.290.49）はこの文言を IAM の反映待ちとして再試行しないので、時間のかかる規程のアップロードの後に作る
    knowledgeBase.node.addDependency(deployment);

    const dataSource = new bedrock.CfnDataSource(this, 'DataSource', {
      knowledgeBaseId: knowledgeBase.attrKnowledgeBaseId,
      name: 'policies',
      // データソースを消すときにベクトルを消しにいかない。ベクトルはインデックスごと消えるので、
      // 消す順番の都合で削除が失敗するのを避ける
      dataDeletionPolicy: 'RETAIN',
      dataSourceConfiguration: {
        type: 'S3',
        s3Configuration: {
          bucketArn: this.docsBucket.bucketArn,
          inclusionPrefixes: [DOCS_PREFIX],
        },
      },
      // 既定と同じ値だが、本で説明できるよう明示しておく
      vectorIngestionConfiguration: {
        chunkingConfiguration: {
          chunkingStrategy: 'FIXED_SIZE',
          fixedSizeChunkingConfiguration: { maxTokens: 300, overlapPercentage: 20 },
        },
      },
    });

    this.knowledgeBaseId = knowledgeBase.attrKnowledgeBaseId;
    this.knowledgeBaseArn = knowledgeBase.attrKnowledgeBaseArn;
    this.dataSourceId = dataSource.attrDataSourceId;
  }
}
