"""
Bedrock Knowledge Base — the Pulumi equivalent of the book's Chapter 2
console walkthrough ("Quick create" RAG knowledge base). This is a fully
*managed* knowledge base: Amazon Bedrock owns the embedding model and the
vector store internally, so there's no OpenSearch Serverless / S3 Vectors
bucket to provision here — just the knowledge base, one S3 data source
pointed at the project bucket, and the IAM role Bedrock assumes to read it.

The IAM role and its two policies below reproduce, field-for-field, what
AWS's own console "Quick create" flow generates — they were adopted via
`pulumi import` onto an already-live console-created role rather than
freshly created, so the exact scoping (not the styling) matters here more
than usual. Don't "clean up" the two-policy split into one inline policy;
it won't match the live resource on the next `pulumi up`.

No idle GPU/compute cost here — storage and per-query cost only, same
profile as the S3 bucket. That's why this lives in `base/`, not the
GPU-billed `endpoints/` project.
"""

import json

import pulumi
import pulumi_aws as aws


def create_bedrock_knowledge_base(
    name_prefix: str,
    bucket_name: pulumi.Output[str],
    bucket_arn: pulumi.Output[str],
) -> tuple[aws.bedrock.AgentKnowledgeBase, aws.bedrock.AgentDataSource]:
    account_id = aws.get_caller_identity().account_id
    region = aws.get_region().region

    # These four names/suffix come from AWS's own console "Quick create"
    # flow (a randomly generated "_isltx" suffix) — adopted via `pulumi
    # import` onto already-live resources rather than freshly created.
    # IAM role/policy names and the KB/data-source names are immutable, so
    # these must stay exactly as-is or the next `pulumi up` will try to
    # replace (destroy + recreate) live, already-ingested resources.
    role_name = "AmazonBedrockExecutionRoleForKnowledgeBase_isltx"
    cloudwatch_policy_name = "AmazonBedrockCloudWatchPolicyForKnowledgeBase_isltx"
    s3_policy_name = "AmazonBedrockS3PolicyForKnowledgeBase_isltx"
    knowledge_base_name = "knowledge-base-quick-start-isltx"
    data_source_name = "knowledge-base-quick-start-isltx-data-source"

    assume_role_policy = json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "TrustPolicyStatement",
                    "Effect": "Allow",
                    "Principal": {"Service": "bedrock.amazonaws.com"},
                    "Action": "sts:AssumeRole",
                    "Condition": {
                        "StringEquals": {"aws:SourceAccount": account_id},
                        "ArnLike": {
                            "aws:SourceArn": f"arn:aws:bedrock:{region}:{account_id}:knowledge-base/*"
                        },
                    },
                }
            ],
        }
    )

    cloudwatch_policy = aws.iam.Policy(
        f"{name_prefix}-kb-cloudwatch-policy",
        name=cloudwatch_policy_name,
        path="/service-role/",
        policy=json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Sid": "CloudWatchWritePermissionStatement",
                        "Effect": "Allow",
                        "Action": ["cloudwatch:PutMetricData"],
                        "Resource": ["*"],
                        "Condition": {
                            "StringEquals": {"cloudwatch:namespace": "AWS/Bedrock/KnowledgeBases"}
                        },
                    }
                ],
            }
        ),
    )

    s3_policy = aws.iam.Policy(
        f"{name_prefix}-kb-s3-policy",
        name=s3_policy_name,
        path="/service-role/",
        policy=bucket_arn.apply(
            lambda arn: json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Sid": "S3ListBucketStatement",
                            "Effect": "Allow",
                            "Action": ["s3:ListBucket"],
                            "Resource": [arn],
                            "Condition": {"StringEquals": {"aws:ResourceAccount": [account_id]}},
                        },
                        {
                            "Sid": "S3GetObjectStatement",
                            "Effect": "Allow",
                            "Action": ["s3:GetObject"],
                            "Resource": [f"{arn}/*"],
                            "Condition": {"StringEquals": {"aws:ResourceAccount": [account_id]}},
                        },
                    ],
                }
            )
        ),
    )

    # pulumi-aws manages this role's policy attachments inline via
    # managed_policy_arns rather than separate RolePolicyAttachment
    # resources — matches what `pulumi import` generated from the live
    # resource; using separate attachment resources here would fight it.
    role = aws.iam.Role(
        f"{name_prefix}-kb-role",
        name=role_name,
        path="/service-role/",
        assume_role_policy=assume_role_policy,
        description="Bedrock Knowledge Base access",
        managed_policy_arns=[cloudwatch_policy.arn, s3_policy.arn],
    )

    knowledge_base = aws.bedrock.AgentKnowledgeBase(
        f"{name_prefix}-knowledge-base",
        name=knowledge_base_name,
        role_arn=role.arn,
        knowledge_base_configuration=aws.bedrock.AgentKnowledgeBaseKnowledgeBaseConfigurationArgs(
            type="MANAGED",
            managed_knowledge_base_configuration=aws.bedrock.AgentKnowledgeBaseKnowledgeBaseConfigurationManagedKnowledgeBaseConfigurationArgs(
                embedding_model_type="MANAGED",
            ),
        ),
    )

    data_source = aws.bedrock.AgentDataSource(
        f"{name_prefix}-kb-data-source",
        name=data_source_name,
        knowledge_base_id=knowledge_base.id,
        data_source_configuration=aws.bedrock.AgentDataSourceDataSourceConfigurationArgs(
            type="MANAGED_KNOWLEDGE_BASE_CONNECTOR",
            managed_knowledge_base_connector_configuration=bucket_name.apply(
                lambda name: aws.bedrock.AgentDataSourceDataSourceConfigurationManagedKnowledgeBaseConnectorConfigurationArgs(
                    connector_parameters=json.dumps(
                        {
                            "type": "S3",
                            "filterConfiguration": {"maxFileSizeInMegaBytes": "10240"},
                            "connectionConfiguration": {
                                "bucketName": name,
                                "bucketOwnerAccountId": account_id,
                                "bucketArn": f"arn:aws:s3:::{name}",
                            },
                            "aclEnabled": False,
                            "version": "1",
                        },
                        # Compact separators to match the string AWS stores
                        # byte-for-byte; json.dumps' default ", " / ": " causes
                        # a spurious in-place update on every `pulumi up`.
                        separators=(",", ":"),
                    ),
                    deletion_protection_configuration=aws.bedrock.AgentDataSourceDataSourceConfigurationManagedKnowledgeBaseConnectorConfigurationDeletionProtectionConfigurationArgs(
                        deletion_protection_status="DISABLED",
                        deletion_protection_threshold=15,
                    ),
                    media_extraction_configuration=aws.bedrock.AgentDataSourceDataSourceConfigurationManagedKnowledgeBaseConnectorConfigurationMediaExtractionConfigurationArgs(
                        image_extraction_configuration=aws.bedrock.AgentDataSourceDataSourceConfigurationManagedKnowledgeBaseConnectorConfigurationMediaExtractionConfigurationImageExtractionConfigurationArgs(
                            image_extraction_status="ENABLED",
                        ),
                        audio_extraction_configuration=aws.bedrock.AgentDataSourceDataSourceConfigurationManagedKnowledgeBaseConnectorConfigurationMediaExtractionConfigurationAudioExtractionConfigurationArgs(
                            audio_extraction_status="ENABLED",
                        ),
                        video_extraction_configuration=aws.bedrock.AgentDataSourceDataSourceConfigurationManagedKnowledgeBaseConnectorConfigurationMediaExtractionConfigurationVideoExtractionConfigurationArgs(
                            video_extraction_status="ENABLED",
                        ),
                    ),
                )
            ),
        ),
        vector_ingestion_configuration=aws.bedrock.AgentDataSourceVectorIngestionConfigurationArgs(
            parsing_configuration=aws.bedrock.AgentDataSourceVectorIngestionConfigurationParsingConfigurationArgs(
                parsing_strategy="SMART_PARSING",
            ),
        ),
        data_deletion_policy="DELETE",
    )

    return knowledge_base, data_source
