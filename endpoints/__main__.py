"""
Entry point for model endpoints — the one part of this project that's
actually expensive (GPU-backed real-time SageMaker endpoints, billed
continuously per hour until torn down).

Deliberately a separate Pulumi project from `../base`, so a routine base
`pulumi up` (bucket/IAM/Studio/guardrails) can never diff or touch these.
Run `pulumi up` here only when you actually want an endpoint running, and
remove its entry from Pulumi.<stack>.yaml + `pulumi up` again the moment
you're done with it — see infrastructure/model_endpoints.py.

Reads the SageMaker execution role from the `ml-engineering-base` stack
instead of creating its own IAM — see the README for the two-project setup.
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pulumi

from infrastructure.model_endpoints import create_huggingface_endpoints

config = pulumi.Config()
project_name = config.get("project_name") or "mle-aws-2e"

# Model endpoints declared for the current chapter(s). Empty by default —
# add entries here as you reach each chapter's deploy exercise. Each entry
# needs an image_uri resolved in Studio — see the README.
endpoint_specs = config.get_object("endpoints") or []

base_stack_name = config.get("base_stack") or f"organization/ml-engineering-base/{pulumi.get_stack()}"
base = pulumi.StackReference(base_stack_name)
execution_role_arn = base.get_output("execution_role_arn")

endpoints = create_huggingface_endpoints(project_name, execution_role_arn, endpoint_specs)

pulumi.export("endpoint_names", [e.name for e in endpoints])
