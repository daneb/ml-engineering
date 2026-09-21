"""
IAM — the SageMaker Studio execution role.

This replicates the permissions the book has you click together by hand in
the IAM console (Chapter 1, "Configuring IAM permissions for your SageMaker
Studio Space"). Everything SageMaker Studio itself does — running notebooks,
training jobs, deploying endpoints — happens *as* this role.

Scope notes for a learning account:
  - AmazonSageMakerFullAccess is attached because the book moves across many
    SageMaker features (Studio, Processing, Pipelines, Feature Store,
    JumpStart) and re-deriving a minimal policy per chapter would slow you
    down. It's broad but scoped to the SageMaker service surface.
  - The inline policy below adds only what SageMakerFullAccess does NOT
    already cover: Bedrock model invocation (for the AgentCore / Bedrock
    chapters) and access to the project's own S3 bucket.
  - This role has no IAM-modifying permissions, so a compromised notebook
    can't escalate itself to account-admin.
"""

import json

import pulumi
import pulumi_aws as aws


def create_sagemaker_execution_role(bucket_arn: pulumi.Output[str], name_prefix: str) -> aws.iam.Role:
    assume_role_policy = json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Service": "sagemaker.amazonaws.com"},
                    "Action": "sts:AssumeRole",
                }
            ],
        }
    )

    role = aws.iam.Role(
        f"{name_prefix}-sagemaker-execution-role",
        name=f"{name_prefix}-sagemaker-studio-execution",
        assume_role_policy=assume_role_policy,
        description="Execution role for SageMaker Studio - used by notebooks, training jobs, and endpoints in the book's exercises.",
    )

    aws.iam.RolePolicyAttachment(
        f"{name_prefix}-sagemaker-full-access",
        role=role.name,
        policy_arn="arn:aws:iam::aws:policy/AmazonSageMakerFullAccess",
    )

    # Bedrock + project-bucket access, scoped down rather than using another
    # full-access managed policy.
    inline_policy_document = bucket_arn.apply(
        lambda arn: json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Sid": "BedrockModelAccess",
                        "Effect": "Allow",
                        "Action": [
                            "bedrock:InvokeModel",
                            "bedrock:InvokeModelWithResponseStream",
                            "bedrock:ListFoundationModels",
                            "bedrock:GetFoundationModel",
                        ],
                        "Resource": "*",
                    },
                    {
                        "Sid": "ProjectBucketAccess",
                        "Effect": "Allow",
                        "Action": [
                            "s3:GetObject",
                            "s3:PutObject",
                            "s3:DeleteObject",
                            "s3:ListBucket",
                        ],
                        "Resource": [arn, f"{arn}/*"],
                    },
                ],
            }
        )
    )

    aws.iam.RolePolicy(
        f"{name_prefix}-sagemaker-inline-policy",
        role=role.id,
        policy=inline_policy_document,
    )

    return role
