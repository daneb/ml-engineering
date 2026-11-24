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

2. Live resources (SageMaker Studio apps, endpoints, and in-progress
   training/processing/transform jobs; Chapter 4 EMR clusters and S3 table
   buckets) — this IS real time. It answers "is anything actually running and billing right
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

# Same set stop_all_apps.py uses: the service isn't usable on this account
# (e.g. EMR on the Free Plan), so nothing of that type can be billing.
SERVICE_UNAVAILABLE_CODES = {"SubscriptionRequiredException", "OptInRequired"}

# EMR cluster states that have EC2 instances attached.
ACTIVE_EMR_STATES = ["STARTING", "BOOTSTRAPPING", "RUNNING", "WAITING"]

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

# Chapter 4 EMR nodes are plain EC2 instances plus an EMR surcharge per
# instance-hour (EC2 on-demand + EMR fee, us-east-1). Rough, same caveat.
EMR_HOURLY_RATE_USD = {
    "m5.large": 0.12,
    "m5.xlarge": 0.24,
    "m5.2xlarge": 0.48,
    "m5.4xlarge": 0.96,
    "c5.xlarge": 0.21,
    "r5.xlarge": 0.31,
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
        print("  No SageMaker compute is currently billing.")


def print_emr_clusters(session: boto3.Session, region: str) -> None:
    """Chapter 4's EMR clusters: EC2 instance-hours that SageMaker calls can't see."""
    print()
    print("=" * 60)
    print(f"EMR CLUSTERS ({region}) — Chapter 4, bills per instance-hour even when idle")
    print("=" * 60)
    emr = session.client("emr", region_name=region)
    now = datetime.now(timezone.utc)
    try:
        clusters = [
            c
            for page in emr.get_paginator("list_clusters").paginate(ClusterStates=ACTIVE_EMR_STATES)
            for c in page["Clusters"]
        ]
        if not clusters:
            print("  No active EMR clusters.")
            return
        for c in clusters:
            groups = emr.list_instance_groups(ClusterId=c["Id"])["InstanceGroups"]
            created = c["Status"]["Timeline"].get("CreationDateTime")
            hrs = (now - created).total_seconds() / 3600 if created else None
            hourly, unknown = 0.0, False
            parts = []
            for g in groups:
                count = g.get("RunningInstanceCount", 0)
                itype = g["InstanceType"]
                parts.append(f"{count}x {itype} ({g['InstanceGroupType'].lower()})")
                rate = EMR_HOURLY_RATE_USD.get(itype)
                if rate is None:
                    unknown = True
                else:
                    hourly += rate * count
            if unknown or hrs is None:
                est = "unknown rate"
            else:
                est = f"~${hourly:.2f}/h, ~${hourly * hrs:.2f} so far"
            hrs_str = f"{hrs:.1f}h" if hrs is not None else "unknown"
            print(
                f"  [EMR]      {c['Name']} ({c['Id']}) [{c['Status']['State']}] "
                f"{', '.join(parts)} — up {hrs_str} — {est}"
            )
    except ClientError as e:
        if e.response["Error"]["Code"] in SERVICE_UNAVAILABLE_CODES:
            print("  EMR isn't enabled on this account (Free Plan restriction) — no clusters can exist.")
        else:
            print(f"  Could not list EMR clusters: {e}")


def print_table_buckets(session: boto3.Session, region: str) -> None:
    """S3 Tables buckets from Chapter 4: storage only, but easy to forget — no teardown step in the book."""
    print()
    print("=" * 60)
    print(f"S3 TABLE BUCKETS ({region}) — storage only, not auto-deleted")
    print("=" * 60)
    try:
        s3t = session.client("s3tables", region_name=region)
        buckets = [
            b
            for page in s3t.get_paginator("list_table_buckets").paginate()
            for b in page["tableBuckets"]
        ]
    except UnknownServiceError:
        print("  Your installed boto3/botocore is too old to know about 's3tables'.")
        print("  Run: pip install --upgrade boto3")
        return
    except ClientError as e:
        print(f"  Could not list table buckets: {e}")
        return
    if not buckets:
        print("  No table buckets.")
        return
    for b in buckets:
        print(f"  [TableBucket] {b['name']} (created {b['createdAt']:%Y-%m-%d})")
    print("  Delete tables, namespaces, then the bucket when you're done with Chapter 4.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--region", default="us-east-1")
    args = parser.parse_args()

    session = boto3.Session(profile_name=args.profile)
    print_credit_balance(session)
    print_live_resources(session, args.region)
    print_emr_clusters(session, args.region)
    print_table_buckets(session, args.region)


if __name__ == "__main__":
    sys.exit(main())
