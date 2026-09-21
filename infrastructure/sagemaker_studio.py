"""
SageMaker Studio — the domain + a single user profile.

This is the Pulumi equivalent of the book's Chapter 1 console walkthrough
("Setting up and configuring your SageMaker Studio environment"). One
domain per AWS account/region is normal — you don't need a new one per
chapter.
"""

from typing import Sequence

import pulumi
import pulumi_aws as aws


def create_studio_domain(
    name_prefix: str,
    vpc_id: str,
    subnet_ids: Sequence[str],
    execution_role_arn: pulumi.Output[str],
    studio_user_name: str,
) -> tuple[aws.sagemaker.Domain, aws.sagemaker.UserProfile]:
    # NOTE: this default only applies when an app is created without an
    # explicit instance type (e.g. via CLI/CloudFormation) — Studio's own
    # UI dropdown does NOT read this value and always lets you pick freely.
    # It's a nudge, not a gate. See guardrails.py for why the real gate
    # can't reach the Studio kernel picker.
    domain = aws.sagemaker.Domain(
        f"{name_prefix}-studio-domain",
        domain_name=f"{name_prefix}-studio",
        auth_mode="IAM",
        vpc_id=vpc_id,
        subnet_ids=subnet_ids,
        app_network_access_type="PublicInternetOnly",
        default_user_settings=aws.sagemaker.DomainDefaultUserSettingsArgs(
            execution_role=execution_role_arn,
            jupyter_lab_app_settings=aws.sagemaker.DomainDefaultUserSettingsJupyterLabAppSettingsArgs(
                default_resource_spec=aws.sagemaker.DomainDefaultUserSettingsJupyterLabAppSettingsDefaultResourceSpecArgs(
                    instance_type="ml.t3.medium",
                ),
            ),
        ),
    )

    user_profile = aws.sagemaker.UserProfile(
        f"{name_prefix}-studio-user",
        domain_id=domain.id,
        user_profile_name=studio_user_name,
        user_settings=aws.sagemaker.UserProfileUserSettingsArgs(
            execution_role=execution_role_arn,
        ),
    )

    return domain, user_profile
