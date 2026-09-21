#!/usr/bin/env python3
"""
Global resource stop — end-of-session cleanup for the AWS credits.

Run this before you close your laptop for the day/week. It queries the
live AWS account directly (not Pulumi state), so it catches everything
currently billing regardless of what created it — this Pulumi stack,
a notebook's own `.deploy()` call, or anything clicked together by hand
in the console:

1. Stops every running Studio App (JupyterLab/CodeEditor kernel) — bills
   by the hour whether you're actively using it or not.
2. Lists any real-time SageMaker endpoints still running and asks before
   deleting them — endpoints bill 24/7 until deleted. This is the exact
   thing that burned the first $100.
3. Stops every running classic Notebook Instance (the older, non-Studio
   notebook type some chapters may still use) — same 24/7-while-running
   billing model as Studio apps.
4. Lists any in-progress Training Jobs. These are NOT auto-stopped (a job
   you're deliberately running shouldn't be killed out from under you),
   but you're shown them so a job you forgot about doesn't run unnoticed
   — training jobs have a max runtime cap, but that cap can still be hours.

What this deliberately does NOT touch: the Studio domain, IAM role, and
S3 bucket. Those cost effectively nothing idle (domain: $0; bucket: cents;
Studio EFS storage: a few cents/GB-month) — leaving them up between
sessions is fine and saves you re-running `pulumi up` every time.

Usage:
    python scripts/stop_all_apps.py --profile mle-aws --region us-east-1
"""

import argparse
import sys

import boto3


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


def list_and_confirm_endpoints(sm) -> None:
    endpoints = sm.list_endpoints(StatusEquals="InService")["Endpoints"]

    if not endpoints:
        print("No in-service endpoints found.")
        return

    print(f"\nFound {len(endpoints)} running endpoint(s) — these bill 24/7 until deleted:")
    for ep in endpoints:
        print(f"  - {ep['EndpointName']} (created {ep['CreationTime']})")

    answer = input("\nDelete ALL of these endpoints now? [y/N] ").strip().lower()
    if answer != "y":
        print("Leaving endpoints running. Remember they're still billing.")
        return

    for ep in endpoints:
        sm.delete_endpoint(EndpointName=ep["EndpointName"])
        print(f"Deleted endpoint: {ep['EndpointName']}")


def stop_notebook_instances(sm) -> None:
    instances = sm.list_notebook_instances(StatusEquals="InService")["NotebookInstances"]

    if not instances:
        print("No in-service classic notebook instances found.")
        return

    print(f"\nFound {len(instances)} running notebook instance(s):")
    for ni in instances:
        print(f"  - {ni['NotebookInstanceName']} ({ni['InstanceType']})")

    answer = input("\nStop ALL of these notebook instances now? [y/N] ").strip().lower()
    if answer != "y":
        print("Leaving notebook instances running. Remember they're still billing.")
        return

    for ni in instances:
        sm.stop_notebook_instance(NotebookInstanceName=ni["NotebookInstanceName"])
        print(f"Stopping notebook instance: {ni['NotebookInstanceName']}")


def list_training_jobs(sm) -> None:
    jobs = sm.list_training_jobs(StatusEquals="InProgress")["TrainingJobSummaries"]

    if not jobs:
        print("No in-progress training jobs found.")
        return

    print(f"\nFound {len(jobs)} in-progress training job(s) — NOT auto-stopped, check these manually:")
    for job in jobs:
        print(f"  - {job['TrainingJobName']} (started {job['CreationTime']})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True, help="AWS CLI profile name")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--domain-id", default=None, help="Restrict app cleanup to one Studio domain")
    args = parser.parse_args()

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    sm = session.client("sagemaker")

    stop_all_apps(sm, args.domain_id)
    list_and_confirm_endpoints(sm)
    stop_notebook_instances(sm)
    list_training_jobs(sm)


if __name__ == "__main__":
    sys.exit(main())
