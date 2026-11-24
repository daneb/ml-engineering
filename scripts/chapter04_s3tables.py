#!/usr/bin/env python3
"""
Chapter 4 (managed transactional data lake) without an EMR cluster.

The book launches EMR, SSHes in and runs Spark SQL against an S3 Tables
bucket. The dataset is small enough to handle on a laptop, so that cluster is
pure cost (and EMR is blocked on the Free Plan anyway). This does the same Iceberg work from
your laptop with PyIceberg, which talks to the S3 Tables Iceberg REST
endpoint directly — no Java, no Spark, nothing left running.

Steps, in the order the chapter does them:

    create-bucket   Create the S3 table bucket (storage only, no compute).
    load            Create namespace + Iceberg v2 table, append the
                    "up to 2024" CSV then the "2025 onwards" CSV. Each
                    append is a separate snapshot.
    snapshots       List snapshots and row count at each (time travel).
    drop-column     Drop the `score` column (schema evolution step).
    teardown        Delete table, namespace and table bucket (ASKS first).

Usage (from the repo root):
    venv/bin/python scripts/chapter04_s3tables.py create-bucket --profile mle-aws
    venv/bin/python scripts/chapter04_s3tables.py load --profile mle-aws
    venv/bin/python scripts/chapter04_s3tables.py snapshots --profile mle-aws
    venv/bin/python scripts/chapter04_s3tables.py teardown --profile mle-aws

`load` is not idempotent: it fails if the table exists rather than silently
replacing it or duplicating rows. Run `teardown` first to start over.
"""

import argparse
import io
import os
import sys
import uuid
import urllib.request

import boto3
import pyarrow as pa
import pyarrow.csv as pacsv
from botocore.exceptions import ClientError
from pyiceberg.catalog import load_catalog

DEFAULT_BUCKET = "mle-aws-2e-ch04-tables"
NAMESPACE = "sentiments_namespace"
TABLE = "sentiments_v1"

RAW_BASE = (
    "https://raw.githubusercontent.com/PacktPublishing/"
    "Machine-Learning-Engineering-on-AWS-Second-Edition/main/chapter04"
)
CSV_FILES = ["data_up_to_2024.csv", "data_2025_onwards.csv"]


def confirm(prompt: str) -> bool:
    return input(f"\n{prompt} [y/N] ").strip().lower() == "y"


def find_bucket_arn(s3tables, name: str) -> str | None:
    for page in s3tables.get_paginator("list_table_buckets").paginate():
        for b in page["tableBuckets"]:
            if b["name"] == name:
                return b["arn"]
    return None


def require_bucket_arn(s3tables, name: str) -> str:
    arn = find_bucket_arn(s3tables, name)
    if arn is None:
        sys.exit(f"No table bucket named '{name}'. Run `create-bucket` first.")
    return arn


def get_catalog(region: str, bucket_arn: str):
    """PyIceberg catalog over the S3 Tables Iceberg REST endpoint (SigV4-signed)."""
    return load_catalog(
        "s3tables",
        **{
            "type": "rest",
            "uri": f"https://s3tables.{region}.amazonaws.com/iceberg",
            "warehouse": bucket_arn,
            "rest.sigv4-enabled": "true",
            "rest.signing-name": "s3tables",
            "rest.signing-region": region,
            "s3.region": region,
        },
    )


def read_csv_with_ids(filename: str) -> pa.Table:
    """Download a chapter CSV and add the unique_id column the book adds."""
    with urllib.request.urlopen(f"{RAW_BASE}/{filename}") as resp:
        data = resp.read()
    table = pacsv.read_csv(io.BytesIO(data))
    ids = pa.array([str(uuid.uuid4()) for _ in range(table.num_rows)], type=pa.string())
    return table.append_column("unique_id", ids)


def cmd_create_bucket(s3tables, args) -> None:
    arn = find_bucket_arn(s3tables, args.bucket)
    if arn:
        print(f"Table bucket already exists: {arn}")
        return
    arn = s3tables.create_table_bucket(name=args.bucket)["arn"]
    print(f"Created table bucket: {arn}")


def cmd_load(s3tables, args) -> None:
    catalog = get_catalog(args.region, require_bucket_arn(s3tables, args.bucket))
    catalog.create_namespace_if_not_exists(NAMESPACE)

    first, *rest = CSV_FILES
    data = read_csv_with_ids(first)
    # Raises TableAlreadyExistsError if re-run — deliberate, see module docstring.
    table = catalog.create_table(
        f"{NAMESPACE}.{TABLE}",
        schema=data.schema,
        properties={"format-version": "2"},
    )
    table.append(data)
    print(f"Appended {data.num_rows} rows from {first}")
    for filename in rest:
        data = read_csv_with_ids(filename)
        table.append(data)
        print(f"Appended {data.num_rows} rows from {filename}")
    print(f"\n{NAMESPACE}.{TABLE} now has {len(table.scan().to_arrow())} rows.")


def cmd_snapshots(s3tables, args) -> None:
    catalog = get_catalog(args.region, require_bucket_arn(s3tables, args.bucket))
    table = catalog.load_table(f"{NAMESPACE}.{TABLE}")
    print(f"Columns: {', '.join(table.schema().column_names)}")
    for snap in table.snapshots():
        rows = table.scan(snapshot_id=snap.snapshot_id).to_arrow().num_rows
        print(f"  snapshot {snap.snapshot_id}  {snap.timestamp_ms}  rows={rows}")
    print(f"\nCurrent: {table.scan().to_arrow().num_rows} rows")
    print("Time travel: table.scan(snapshot_id=<id>).to_arrow()")


def cmd_drop_column(s3tables, args) -> None:
    catalog = get_catalog(args.region, require_bucket_arn(s3tables, args.bucket))
    table = catalog.load_table(f"{NAMESPACE}.{TABLE}")
    if "score" not in table.schema().column_names:
        sys.exit("No `score` column to drop (already dropped, or not in this dataset).")
    with table.update_schema() as update:
        update.delete_column("score")
    print(f"Dropped `score`. Columns now: {', '.join(table.schema().column_names)}")


def cmd_teardown(s3tables, args) -> None:
    arn = find_bucket_arn(s3tables, args.bucket)
    if arn is None:
        print(f"No table bucket named '{args.bucket}'. Nothing to delete.")
        return
    if not confirm(
        f"Delete table {NAMESPACE}.{TABLE}, namespace {NAMESPACE} and table bucket "
        f"'{args.bucket}'? (not reversible)"
    ):
        print("Left everything in place.")
        return
    for step, call in [
        ("table", lambda: s3tables.delete_table(tableBucketARN=arn, namespace=NAMESPACE, name=TABLE)),
        ("namespace", lambda: s3tables.delete_namespace(tableBucketARN=arn, namespace=NAMESPACE)),
        ("table bucket", lambda: s3tables.delete_table_bucket(tableBucketARN=arn)),
    ]:
        try:
            call()
            print(f"Deleted {step}.")
        except ClientError as e:
            if e.response["Error"]["Code"] != "NotFoundException":
                raise
            print(f"{step.capitalize()} already gone.")


COMMANDS = {
    "create-bucket": cmd_create_bucket,
    "load": cmd_load,
    "snapshots": cmd_snapshots,
    "drop-column": cmd_drop_column,
    "teardown": cmd_teardown,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=COMMANDS)
    parser.add_argument("--profile", required=True, help="AWS CLI profile name")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--bucket", default=DEFAULT_BUCKET, help="Table bucket name")
    args = parser.parse_args()

    # PyIceberg's SigV4 signing and its S3 file IO both use the default
    # credential chain, so the profile is passed through the environment.
    os.environ["AWS_PROFILE"] = args.profile
    os.environ["AWS_DEFAULT_REGION"] = args.region

    s3tables = boto3.Session(profile_name=args.profile, region_name=args.region).client("s3tables")
    COMMANDS[args.command](s3tables, args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
