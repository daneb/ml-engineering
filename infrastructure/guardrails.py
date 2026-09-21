"""
Cost guardrail #1 — a hard IAM Deny on oversized SageMaker instance types.

This is what would have stopped the ml.g5.25xlarge deployment before it
ever started. It attaches an explicit Deny to the execution role for every
SageMaker API call that lets you pick an instance type. Deny always wins
over Allow in IAM, so this overrides AmazonSageMakerFullAccess even though
that policy has no instance-type restriction of its own.

WHAT THIS COVERS (confirmed via AWS's own condition-key documentation —
these are the only actions AWS extends `sagemaker:InstanceTypes` to):
  - CreateTrainingJob / CreateHyperParameterTuningJob   (training)
  - CreateProcessingJob / CreateTransformJob            (processing/batch)
  - CreateEndpointConfig                                (hosted endpoints —
    this is the one that actually would have blocked your g5.25xlarge)
  - CreateNotebookInstance / UpdateNotebookInstance     (classic notebooks)
  - CreateLabelingJob

WHAT THIS DOES NOT COVER (an AWS platform limitation, not a gap in this
policy — AWS has never extended this condition key to the action below,
and other users have hit the same wall: see
github.com/aws/amazon-sagemaker-examples/issues/1499):
  - CreateApp — the call SageMaker Studio makes when you pick a compute
    size for a JupyterLab/CodeEditor kernel from the Studio UI dropdown.
    There is no IAM condition key that restricts this. The mitigation here
    is the default instance type set on the Studio domain (see
    sagemaker_studio.py) plus deliberate choice at the dropdown — the
    small-model list in the README tells you what you actually need there.
"""

import json
from typing import Sequence

import pulumi
import pulumi_aws as aws

GATED_ACTIONS = [
    "sagemaker:CreateTrainingJob",
    "sagemaker:CreateHyperParameterTuningJob",
    "sagemaker:CreateProcessingJob",
    "sagemaker:CreateTransformJob",
    "sagemaker:CreateEndpointConfig",
    "sagemaker:CreateNotebookInstance",
    "sagemaker:UpdateNotebookInstance",
    "sagemaker:CreateLabelingJob",
]


def create_instance_type_guardrail(
    role: aws.iam.Role,
    name_prefix: str,
    allowed_instance_patterns: Sequence[str],
) -> aws.iam.RolePolicy:
    policy_document = json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "DenyOversizedSageMakerInstances",
                    "Effect": "Deny",
                    "Action": GATED_ACTIONS,
                    "Resource": "*",
                    "Condition": {
                        "ForAnyValue:StringNotLike": {
                            "sagemaker:InstanceTypes": list(allowed_instance_patterns)
                        }
                    },
                }
            ],
        }
    )

    return aws.iam.RolePolicy(
        f"{name_prefix}-instance-type-guardrail",
        role=role.id,
        policy=policy_document,
    )
