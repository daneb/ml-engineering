"""
Model endpoints — Hugging Face models hosted on SageMaker, one per chapter
exercise, declared as config rather than code.

The container image URI is passed in as plain config, NOT resolved here
via the `sagemaker` SDK. That SDK pulls in scipy/numpy/pandas and a long
chain of ML tooling just to look up one string, and pinning it inside this
lightweight infra venv turned into real dependency hell (scipy/numpy
incompatibilities under a new Python version). That lookup belongs in
Studio, where AWS already ships a properly pinned, working environment —
see the README for the one-line snippet to run there to get the URI.

To add an endpoint: add an entry to `ml-engineering:endpoints` in
Pulumi.dev.yaml (including its image_uri), then `pulumi up`.

To remove one: delete its entry from that list, then `pulumi up` — Pulumi
diffs the declared resources against what exists and destroys only the
endpoint that's no longer declared. This never touches the domain, role,
bucket, or guardrails; there is no need to `pulumi destroy` the whole stack
to tear down a single chapter's endpoint.
"""

from typing import Sequence, TypedDict

import pulumi
import pulumi_aws as aws


class EndpointSpec(TypedDict):
    name: str
    hf_model_id: str
    instance_type: str
    image_uri: str


def create_huggingface_endpoints(
    name_prefix: str,
    execution_role_arn: pulumi.Output[str],
    specs: Sequence[EndpointSpec],
) -> list[aws.sagemaker.Endpoint]:
    endpoints = []

    for spec in specs:
        resource_name = f"{name_prefix}-{spec['name']}"

        model = aws.sagemaker.Model(
            f"{resource_name}-model",
            name=f"{resource_name}-model",
            execution_role_arn=execution_role_arn,
            primary_container=aws.sagemaker.ModelPrimaryContainerArgs(
                image=spec["image_uri"],
                # SM_VLLM_MODEL is the AWS vLLM DLC's env var (not the
                # HF_MODEL_ID/HF_TASK pair used by the plain HF Inference
                # Toolkit). The plain toolkit's /invocations handler expects
                # {"inputs": "plain string", "parameters": {...}} and breaks
                # on agent frameworks (e.g. Strands' SageMakerAIModel) that
                # send an OpenAI-style chat payload ({"messages": [...],
                # "max_tokens": ..., ...}). vLLM's /invocations speaks that
                # shape natively.
                environment={
                    "SM_VLLM_MODEL": spec["hf_model_id"],
                },
            ),
        )

        endpoint_config = aws.sagemaker.EndpointConfiguration(
            f"{resource_name}-endpoint-config",
            name=f"{resource_name}-endpoint-config",
            production_variants=[
                aws.sagemaker.EndpointConfigurationProductionVariantArgs(
                    variant_name="AllTraffic",
                    model_name=model.name,
                    initial_instance_count=1,
                    # The instance-type guardrail evaluates this exact
                    # value at CreateEndpointConfig — an oversized entry
                    # here fails the `pulumi up` outright.
                    instance_type=spec["instance_type"],
                )
            ],
        )

        endpoint = aws.sagemaker.Endpoint(
            f"{resource_name}-endpoint",
            name=f"{resource_name}-endpoint",
            endpoint_config_name=endpoint_config.name,
        )

        endpoints.append(endpoint)

    return endpoints
