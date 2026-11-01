"""
Entry point for the base infrastructure — networking, IAM, storage,
SageMaker Studio, and the two cost guardrails. Everything here is cheap and
long-lived; safe to `pulumi up` anytime without risking GPU spend.

Model endpoints (the one part of this project that's actually expensive)
live in the sibling `../endpoints` Pulumi project so they can never be
touched by a routine base `pulumi up` — see that project's __main__.py.

Run `pulumi up` to create everything below. You do NOT need to
`pulumi destroy` between reading sessions — see the README's "Pausing and
resuming" section for what actually costs money vs. what's safe to leave
running for days.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pulumi

from infrastructure.networking import get_default_network
from infrastructure.iam import create_sagemaker_execution_role, grant_knowledge_base_access
from infrastructure.storage import create_ml_bucket
from infrastructure.sagemaker_studio import create_studio_domain
from infrastructure.guardrails import create_instance_type_guardrail
from infrastructure.budget import create_cost_budget
from infrastructure.bedrock_knowledge_base import create_bedrock_knowledge_base

config = pulumi.Config()
project_name = config.get("project_name") or "mle-aws-2e"
studio_user_name = config.get("studio_user_name") or "learner"

# Instance types allowed anywhere the sagemaker:InstanceTypes condition key
# reaches (training jobs, processing/transform jobs, hosted endpoints,
# classic notebook instances). See infrastructure/guardrails.py for exactly
# which API calls this covers and the one thing it can't cover.
allowed_instance_patterns = config.get_object("allowed_instance_patterns") or [
    "ml.t3.*",
    "ml.t2.*",
    "ml.m5.*",
    "ml.c5.*",
    "ml.g4dn.xlarge",
    "ml.g5.xlarge",
    "ml.g5.2xlarge",
]

monthly_budget_usd = config.get("monthly_budget_usd") or "100"
budget_alert_email = config.require("budget_alert_email")

# Networking — default VPC, no custom networking needed for this book.
vpc_id, subnet_ids = get_default_network()

# Storage — one bucket for all datasets/models/pipeline artifacts.
bucket = create_ml_bucket(project_name)

# IAM — the role SageMaker Studio (and everything it launches, including
# endpoints declared in the sibling `endpoints` project) runs as.
execution_role = create_sagemaker_execution_role(bucket.arn, project_name)

# Guardrail #1 — hard Deny on any instance type outside the allow-list, for
# every SageMaker action that supports the condition key.
create_instance_type_guardrail(execution_role, project_name, allowed_instance_patterns)

# Guardrail #2 — budget alerts at 50/80/100% of actual spend.
create_cost_budget(project_name, monthly_budget_usd, budget_alert_email)

# SageMaker Studio — domain + your user profile.
domain, user_profile = create_studio_domain(
    project_name,
    vpc_id,
    subnet_ids,
    execution_role.arn,
    studio_user_name,
)

# Bedrock Knowledge Base — Chapter 2's RAG exercise. Fully managed (AWS owns
# the embedding model and vector store), so no continuous compute cost —
# that's why it lives here rather than in `endpoints/`.
knowledge_base, kb_data_source = create_bedrock_knowledge_base(
    project_name,
    bucket.id,
    bucket.arn,
)

# Let Studio notebooks query the knowledge base (bedrock:Retrieve /
# RetrieveAndGenerate), scoped to this one KB.
grant_knowledge_base_access(execution_role, knowledge_base.arn, project_name)

pulumi.export("bucket_name", bucket.id)
pulumi.export("execution_role_arn", execution_role.arn)
pulumi.export("studio_domain_id", domain.id)
pulumi.export("studio_domain_url", domain.url)
pulumi.export("studio_user_profile_name", user_profile.user_profile_name)
pulumi.export("allowed_instance_patterns", allowed_instance_patterns)
pulumi.export("knowledge_base_id", knowledge_base.id)
pulumi.export("data_source_id", kb_data_source.data_source_id)
