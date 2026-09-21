"""
Storage — a single S3 bucket for datasets, model artifacts, and SageMaker
Processing/Pipelines output. The book has you create ad-hoc buckets per
chapter; this gives you one bucket with clear prefixes instead, so you don't
end up with a dozen forgotten buckets across chapters.

Suggested prefixes once it's up:
  s3://<bucket>/datasets/
  s3://<bucket>/models/
  s3://<bucket>/pipelines/
"""

import pulumi_aws as aws


def create_ml_bucket(name_prefix: str) -> aws.s3.BucketV2:
    bucket = aws.s3.BucketV2(
        f"{name_prefix}-bucket",
        bucket_prefix=f"{name_prefix}-",
    )

    aws.s3.BucketVersioningV2(
        f"{name_prefix}-bucket-versioning",
        bucket=bucket.id,
        versioning_configuration=aws.s3.BucketVersioningV2VersioningConfigurationArgs(
            status="Enabled",
        ),
    )

    aws.s3.BucketServerSideEncryptionConfigurationV2(
        f"{name_prefix}-bucket-encryption",
        bucket=bucket.id,
        rules=[
            aws.s3.BucketServerSideEncryptionConfigurationV2RuleArgs(
                apply_server_side_encryption_by_default=aws.s3.BucketServerSideEncryptionConfigurationV2RuleApplyServerSideEncryptionByDefaultArgs(
                    sse_algorithm="AES256",
                ),
            )
        ],
    )

    aws.s3.BucketPublicAccessBlock(
        f"{name_prefix}-bucket-public-access-block",
        bucket=bucket.id,
        block_public_acls=True,
        block_public_policy=True,
        ignore_public_acls=True,
        restrict_public_buckets=True,
    )

    return bucket
