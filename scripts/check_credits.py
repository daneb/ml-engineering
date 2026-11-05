#!/usr/bin/env python3
"""
Credit + live-resource check for the $100 free-tier account.

Two different signals here, on two different clocks:

1. Free Tier credit balance (GetAccountPlanState) — the authoritative
   dollar figure AWS tracks for your account's $100 free-plan credit.
   This is NOT guaranteed to update instantly — AWS doesn't publish an
   exact refresh interval for this API, so treat a few hours of lag as
   normal. Don't expect this number to move the moment you run
   `pulumi up` — creating the domain/role/bucket costs ~$0, so there's
   nothing for it to reflect yet.

2. Live SageMaker resources (Studio apps, endpoints, and in-progress
   training/processing/transform jobs) — this IS real
   time. It answers "is anything actually running and billing right
   now", which is the more useful question most of the time, since it
   catches spend before it ever shows up in #1.

Usage:
    python scripts/check_credits.py --profile mle-aws
"""

import argparse
import sys
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError, UnknownServiceError

# Rough on-demand hourly rates (us-east-1), used only to estimate what a
# currently-running resource is costing so far this session — not an
# official AWS figure, just a sanity-check multiplier.
HOURLY_RATE_USD = {
    "ml.t3.medium": 0.05,
    "ml.t2.medium": 0.06,
    "ml.m5.xlarge": 0.23,
    "ml.c5.xlarge": 0.19,
    "ml.g4dn.xlarge": 0.74,
    "ml.g5.xlarge": 1.21,
    "ml.g5.2xlarge": 1.52,
}


def print_credit_balance(session: boto3.Session) -> None:
    print("=" * 60)
    print("FREE TIER CREDIT BALANCE (may lag a few hours)")
    print("=" * 60)
    try:
        # Billing/Free Tier APIs are queried against us-east-1 regardless
        # of which region your resources run in.
        ft = session.client("freetier", region_name="us-east-1")
        state = ft.get_account_plan_state()
        credits = state.get("accountPlanRemainingCredits", {})
        print(f"Plan type:        {state.get('accountPlanType', '?')}")
        print(f"Plan status:      {state.get('accountPlanStatus', '?')}")
        print(f"Remaining credit: {credits.get('amount', '?')} {credits.get('unit', '')}")
        print(f"Plan expires:     {state.get('accountPlanExpirationDate', '?')}")
    except UnknownServiceError:
        print("Your installed boto3/botocore is too old to know about the")
        print("'freetier' service. Run: pip install --upgrade boto3")
    except ClientError as e:
        print(f"Could not fetch credit balance: {e}")
        print("(This API needs the account to be on the post-July-2025 Free Plan.")
        print(" If it doesn't exist for this account, use AWS Budgets email alerts instead.)")


def print_live_resources(session: boto3.Session, region: str) -> None:
    print()
    print("=" * 60)
    print(f"LIVE, BILLING-NOW RESOURCES ({region})")
    print("=" * 60)
    sm = session.client("sagemaker", region_name=region)
    now = datetime.now(timezone.utc)
    found_anything = False

    # Studio apps
    paginator = sm.get_paginator("list_apps")
    for page in paginator.paginate():
        for app in page["Apps"]:
            if app["Status"] not in ("InService", "Pending"):
                continue
            found_anything = True
            instance_type = app.get("ResourceSpec", {}).get("InstanceType", "unknown")
            created = app.get("CreationTime")
            duration_hrs = (now - created).total_seconds() / 3600 if created else None
            rate = HOURLY_RATE_USD.get(instance_type)
            est = f"~${rate * duration_hrs:.2f}" if rate and duration_hrs else "unknown rate"
            print(
                f"  [App]      {app['AppName']} ({app['AppType']}) "
                f"on {instance_type} — running {duration_hrs:.1f}h — est. {est}"
            )

    # Endpoints
    for ep in sm.list_endpoints(StatusEquals="InService")["Endpoints"]:
        found_anything = True
        created = ep.get("CreationTime")
        duration_hrs = (now - created).total_seconds() / 3600 if created else None
        # Endpoint instance type isn't in list_endpoints; describe it.
        try:
            desc = sm.describe_endpoint_config(
                EndpointConfigName=sm.describe_endpoint(EndpointName=ep["EndpointName"])["EndpointConfigName"]
            )
            instance_type = desc["ProductionVariants"][0].get("InstanceType", "unknown")
        except ClientError:
            instance_type = "unknown"
        rate = HOURLY_RATE_USD.get(instance_type)
        est = f"~${rate * duration_hrs:.2f}" if rate and duration_hrs else "unknown rate"
        print(
            f"  [Endpoint] {ep['EndpointName']} on {instance_type} "
            f"— running {duration_hrs:.1f}h — est. {est} — BILLS 24/7 UNTIL DELETED"
        )

    # Jobs: bill per second while InProgress and stop on their own.
    def job_line(kind, name, instance_type, count, started):
        duration_hrs = (now - started).total_seconds() / 3600 if started else None
        rate = HOURLY_RATE_USD.get(instance_type)
        est = f"~${rate * count * duration_hrs:.2f}" if rate and duration_hrs else "unknown rate"
        hrs = f"{duration_hrs:.1f}h" if duration_hrs is not None else "unknown"
        print(f"  [{kind}] {name} on {count}x {instance_type} — running {hrs} — est. {est}")

    for summary in sm.list_training_jobs(StatusEquals="InProgress")["TrainingJobSummaries"]:
        found_anything = True
        d = sm.describe_training_job(TrainingJobName=summary["TrainingJobName"])
        rc = d["ResourceConfig"]
        job_line(
            "Training", d["TrainingJobName"], rc.get("InstanceType", "unknown"),
            rc.get("InstanceCount", 1), d.get("TrainingStartTime") or d.get("CreationTime"),
        )

    for summary in sm.list_processing_jobs(StatusEquals="InProgress")["ProcessingJobSummaries"]:
        found_anything = True
        d = sm.describe_processing_job(ProcessingJobName=summary["ProcessingJobName"])
        cc = d["ProcessingResources"]["ClusterConfig"]
        job_line(
            "Processing", d["ProcessingJobName"], cc.get("InstanceType", "unknown"),
            cc.get("InstanceCount", 1), d.get("ProcessingStartTime") or d.get("CreationTime"),
        )

    for summary in sm.list_transform_jobs(StatusEquals="InProgress")["TransformJobSummaries"]:
        found_anything = True
        d = sm.describe_transform_job(TransformJobName=summary["TransformJobName"])
        tr = d["TransformResources"]
        job_line(
            "Transform", d["TransformJobName"], tr.get("InstanceType", "unknown"),
            tr.get("InstanceCount", 1), d.get("TransformStartTime") or d.get("CreationTime"),
        )

    if not found_anything:
        print("  Nothing running. No compute is currently billing.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--region", default="us-east-1")
    args = parser.parse_args()

    session = boto3.Session(profile_name=args.profile)
    print_credit_balance(session)
    print_live_resources(session, args.region)


if __name__ == "__main__":
    sys.exit(main())
