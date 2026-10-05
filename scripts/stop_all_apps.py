#!/usr/bin/env python3
"""
Global resource stop — end-of-session cleanup for the AWS credits.

Run this before you close your laptop for the day/week. It queries the
live AWS account directly (not Pulumi state), so it catches everything
currently billing regardless of what created it — the `base` or
`endpoints` Pulumi stacks, a notebook's own `.deploy()` call, or anything
clicked together by hand in the console.

Stopped or deleted (asks first where marked):

1. Studio Apps (JupyterLab/CodeEditor kernels) — bill by the hour whether
   you're using them or not. Stopped without asking; your files live on the
   space's storage and survive this.
2. Real-time SageMaker endpoints (ASKS) — bill 24/7 until deleted. Includes
   endpoints still Creating/Updating, not just InService: a deploy you
   kicked off five minutes ago is already billing. This is the exact thing
   that burned the first $100.
   If a deleted endpoint is declared in `endpoints/Pulumi.dev.yaml`, you're
   told to remove it there too — otherwise the next `pulumi refresh` +
   `pulumi up` in `endpoints/` quietly recreates the GPU instance.
3. Classic Notebook Instances (ASKS) — same 24/7-while-running billing.
4. EMR clusters (ASKS) — Chapter 4's EMR/Iceberg exercise runs ad hoc from
   Studio, outside Pulumi. A cluster left WAITING bills per instance-hour
   just like a running one.

Listed only, never auto-stopped (a job you're deliberately running
shouldn't be killed out from under you, but a forgotten one shouldn't run
unnoticed either):

5. In-progress Training, Processing (ch6), Batch Transform and
   Hyperparameter Tuning jobs.

Deliberately NOT touched — the `base` stack: Studio domain, IAM roles,
S3 bucket, guardrails, budget, and the Bedrock managed knowledge base.
None of these bill for idle compute (domain: $0; bucket and Studio storage:
cents; the managed KB has no instance behind it). Leaving them up between
sessions is fine and saves you re-running `pulumi up` every time.

Usage:
    python scripts/stop_all_apps.py --profile mle-aws --region us-east-1
"""

import argparse
import pathlib
import sys

import boto3
from botocore.exceptions import ClientError

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
ENDPOINTS_STACK_CONFIG = REPO_ROOT / "endpoints" / "Pulumi.dev.yaml"

# Every endpoint status that means an instance exists (or is about to) and
# is billing. Excludes Deleting (already on its way out), Failed and
# OutOfService (no instance running).
BILLING_ENDPOINT_STATUSES = ("Creating", "Updating", "SystemUpdating", "RollingBack", "InService")

# EMR cluster states that have EC2 instances attached.
ACTIVE_EMR_STATES = ["STARTING", "BOOTSTRAPPING", "RUNNING", "WAITING"]

# Error codes meaning "this service isn't usable on this account" rather than
# a real failure. Free Plan accounts get SubscriptionRequiredException for
# services they can't use until upgraded to a paid plan (EMR is one) — if the
# service can't be used, nothing of that type can be running and billing.
SERVICE_UNAVAILABLE_CODES = {"SubscriptionRequiredException", "OptInRequired"}


def confirm(prompt: str) -> bool:
    return input(f"\n{prompt} [y/N] ").strip().lower() == "y"


# ---------------------------------------------------------------------------
# 1. Studio apps
# ---------------------------------------------------------------------------

def stop_all_apps(sm, domain_id: str | None) -> None:
    paginator = sm.get_paginator("list_apps")
    kwargs = {"DomainIdEquals": domain_id} if domain_id else {}
    stopped = 0

    for page in paginator.paginate(**kwargs):
        for app in page["Apps"]:
            if app["Status"] not in ("InService", "Pending"):
                continue
            owner = app.get("UserProfileName") or app.get("SpaceName") or "?"
            print(f"Stopping app: {app['AppName']} ({app['AppType']}) for {owner}")
            delete_kwargs = {
                "DomainId": app["DomainId"],
                "AppType": app["AppType"],
                "AppName": app["AppName"],
            }
            # delete_app requires exactly one of these two — passing the
            # other explicitly as None fails botocore's param validation,
            # since None isn't a valid value for a string param (it has to
            # be omitted entirely, not passed-and-empty). Studio's current
            # Space-based apps only populate SpaceName; the older
            # per-user-profile app model only populates UserProfileName.
            if app.get("UserProfileName"):
                delete_kwargs["UserProfileName"] = app["UserProfileName"]
            elif app.get("SpaceName"):
                delete_kwargs["SpaceName"] = app["SpaceName"]
            sm.delete_app(**delete_kwargs)
            stopped += 1

    if stopped == 0:
        print("No running Studio apps found — nothing to stop.")
    else:
        print(f"Stopped {stopped} app(s).")


# ---------------------------------------------------------------------------
# 2. Endpoints (+ Pulumi drift warning for the `endpoints/` project)
# ---------------------------------------------------------------------------

def pulumi_declared_endpoint_names() -> set[str]:
    """
    Endpoint names the `endpoints/` Pulumi stack will (re)create, derived the
    same way infrastructure/model_endpoints.py names them:
    f"{project_name}-{spec['name']}-endpoint".
    """
    if not ENDPOINTS_STACK_CONFIG.exists():
        return set()
    try:
        import yaml  # PyYAML ships as a dependency of the pulumi SDK
    except ImportError:
        print("(PyYAML not available — skipping the endpoints/ Pulumi drift check.)")
        return set()

    config = (yaml.safe_load(ENDPOINTS_STACK_CONFIG.read_text()) or {}).get("config", {})
    project_name = config.get("ml-engineering-endpoints:project_name", "mle-aws-2e")
    specs = config.get("ml-engineering-endpoints:endpoints") or []
    return {f"{project_name}-{spec['name']}-endpoint" for spec in specs if "name" in spec}


def list_and_confirm_endpoints(sm) -> None:
    endpoints = [
        ep
        for page in sm.get_paginator("list_endpoints").paginate()
        for ep in page["Endpoints"]
        if ep["EndpointStatus"] in BILLING_ENDPOINT_STATUSES
    ]

    if not endpoints:
        print("No billing endpoints found.")
        return

    declared = pulumi_declared_endpoint_names()

    print(f"\nFound {len(endpoints)} endpoint(s) — these bill 24/7 until deleted:")
    for ep in endpoints:
        source = "Pulumi endpoints/ stack" if ep["EndpointName"] in declared else "not in Pulumi"
        print(f"  - {ep['EndpointName']} [{ep['EndpointStatus']}] "
              f"(created {ep['CreationTime']:%Y-%m-%d %H:%M}, {source})")

    if not confirm("Delete ALL of these endpoints now?"):
        print("Leaving endpoints running. Remember they're still billing.")
        return

    deleted = []
    for ep in endpoints:
        sm.delete_endpoint(EndpointName=ep["EndpointName"])
        deleted.append(ep["EndpointName"])
        print(f"Deleted endpoint: {ep['EndpointName']}")

    drifted = sorted(name for name in deleted if name in declared)
    if drifted:
        print(
            "\n!! Pulumi drift: these endpoints are still declared in "
            "endpoints/Pulumi.dev.yaml:\n"
            + "".join(f"     - {name}\n" for name in drifted)
            + "   Remove their entries there, then run:\n"
            "     cd endpoints && pulumi refresh --yes && pulumi up\n"
            "   Otherwise the next refresh + up in endpoints/ recreates them and\n"
            "   GPU billing starts again."
        )


# ---------------------------------------------------------------------------
# 3. Classic notebook instances
# ---------------------------------------------------------------------------

def stop_notebook_instances(sm) -> None:
    instances = [
        ni
        for page in sm.get_paginator("list_notebook_instances").paginate()
        for ni in page["NotebookInstances"]
        if ni["NotebookInstanceStatus"] in ("Pending", "InService")
    ]

    if not instances:
        print("No running classic notebook instances found.")
        return

    print(f"\nFound {len(instances)} running notebook instance(s):")
    for ni in instances:
        print(f"  - {ni['NotebookInstanceName']} ({ni['InstanceType']}) [{ni['NotebookInstanceStatus']}]")

    if not confirm("Stop ALL of these notebook instances now?"):
        print("Leaving notebook instances running. Remember they're still billing.")
        return

    for ni in instances:
        if ni["NotebookInstanceStatus"] == "Pending":
            # StopNotebookInstance rejects Pending instances; they have to
            # reach InService first.
            print(f"Skipping {ni['NotebookInstanceName']} (still Pending) — re-run the script in a few minutes.")
            continue
        sm.stop_notebook_instance(NotebookInstanceName=ni["NotebookInstanceName"])
        print(f"Stopping notebook instance: {ni['NotebookInstanceName']}")


# ---------------------------------------------------------------------------
# 4. EMR clusters (Chapter 4 — created ad hoc, outside Pulumi)
# ---------------------------------------------------------------------------

def terminate_emr_clusters(emr) -> None:
    try:
        clusters = [
            c
            for page in emr.get_paginator("list_clusters").paginate(ClusterStates=ACTIVE_EMR_STATES)
            for c in page["Clusters"]
        ]
    except ClientError as e:
        if e.response["Error"]["Code"] in SERVICE_UNAVAILABLE_CODES:
            print("EMR isn't enabled on this account (Free Plan restriction) — no clusters can exist. Skipping.")
            return
        raise

    if not clusters:
        print("No active EMR clusters found.")
        return

    print(f"\nFound {len(clusters)} active EMR cluster(s) — these bill per instance-hour, even when WAITING:")
    for c in clusters:
        print(f"  - {c['Name']} ({c['Id']}) [{c['Status']['State']}]")

    if not confirm("TERMINATE all of these clusters now? (not reversible — data on cluster HDFS is lost)"):
        print("Leaving EMR clusters running. Remember they're still billing.")
        return

    emr.terminate_job_flows(JobFlowIds=[c["Id"] for c in clusters])
    for c in clusters:
        print(f"Terminating EMR cluster: {c['Name']} ({c['Id']})")


# ---------------------------------------------------------------------------
# 5. In-progress jobs — listed, never stopped
# ---------------------------------------------------------------------------

def list_in_progress_jobs(sm) -> None:
    # (label, paginator name, response key, name field)
    job_kinds = [
        ("training", "list_training_jobs", "TrainingJobSummaries", "TrainingJobName"),
        ("processing", "list_processing_jobs", "ProcessingJobSummaries", "ProcessingJobName"),
        ("batch transform", "list_transform_jobs", "TransformJobSummaries", "TransformJobName"),
        ("tuning", "list_hyper_parameter_tuning_jobs", "HyperParameterTuningJobSummaries",
         "HyperParameterTuningJobName"),
    ]

    found_any = False
    for label, paginator_name, key, name_field in job_kinds:
        jobs = [
            job
            for page in sm.get_paginator(paginator_name).paginate(StatusEquals="InProgress")
            for job in page[key]
        ]
        if not jobs:
            continue
        found_any = True
        print(f"\nFound {len(jobs)} in-progress {label} job(s) — NOT auto-stopped, check these manually:")
        for job in jobs:
            print(f"  - {job[name_field]} (started {job['CreationTime']:%Y-%m-%d %H:%M})")

    if not found_any:
        print("No in-progress training, processing, transform or tuning jobs found.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--profile", required=True, help="AWS CLI profile name")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--domain-id", default=None, help="Restrict app cleanup to one Studio domain")
    args = parser.parse_args()

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    sm = session.client("sagemaker")
    emr = session.client("emr")

    sections = [
        ("Studio apps", lambda: stop_all_apps(sm, args.domain_id)),
        ("Endpoints", lambda: list_and_confirm_endpoints(sm)),
        ("Classic notebook instances", lambda: stop_notebook_instances(sm)),
        ("EMR clusters", lambda: terminate_emr_clusters(emr)),
        ("In-progress jobs", lambda: list_in_progress_jobs(sm)),
    ]

    # Each section runs independently: one service erroring (permissions,
    # throttling, a service not enabled) must never stop the rest of the
    # cleanup from running — a crash after step 1 would leave endpoints
    # billing unnoticed.
    failed = []
    for title, run in sections:
        print(f"\n== {title} ==")
        try:
            run()
        except ClientError as e:
            err = e.response["Error"]
            print(f"!! {title} check failed: {err['Code']}: {err['Message']}")
            failed.append(title)

    if failed:
        print("\n!! Not checked — verify these manually in the console: " + ", ".join(failed))
        return 1

    print("\nDone. Confirm with: venv/bin/python scripts/check_credits.py --profile", args.profile)
    return 0


if __name__ == "__main__":
    sys.exit(main())
