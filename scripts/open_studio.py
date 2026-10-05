#!/usr/bin/env python3
"""
Open SageMaker Studio with a fresh presigned URL.

The Studio domain uses IAM auth, so the bare domain URL (and any bookmarked
or reused presigned link) fails with "Invalid or Expired Auth Token". This
script asks AWS for a new one-time link and opens it in your browser.

- The link itself must be opened within 5 minutes (AWS's maximum) — this
  script opens it immediately, so that's never an issue.
- The Studio session it starts lasts --session-hours (default 12, the
  maximum). When it expires, just run this again.

Domain ID and user profile come from the `base` Pulumi stack outputs, so
this keeps working after rotating accounts or recreating the domain. Pass
--domain-id / --user to skip the Pulumi lookup (e.g. to avoid the
passphrase prompt).

Usage:
    python scripts/open_studio.py --profile mle-aws
    python scripts/open_studio.py --profile mle-aws --print-only
"""

import argparse
import json
import pathlib
import subprocess
import sys
import webbrowser

import boto3

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
BASE_STACK_DIR = REPO_ROOT / "base"


def base_stack_outputs() -> dict:
    """Read `pulumi stack output --json` from base/. May prompt for the passphrase."""
    try:
        result = subprocess.run(
            ["pulumi", "stack", "output", "--json"],
            cwd=BASE_STACK_DIR,
            check=True,
            stdout=subprocess.PIPE,  # stdin/stderr inherited, so a passphrase prompt still works
            text=True,
        )
    except FileNotFoundError:
        sys.exit("pulumi CLI not found — pass --domain-id and --user instead.")
    except subprocess.CalledProcessError:
        sys.exit("Couldn't read base/ stack outputs — pass --domain-id and --user instead.")
    return json.loads(result.stdout)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--profile", required=True, help="AWS CLI profile name")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--domain-id", help="Studio domain ID (default: base stack output studio_domain_id)")
    parser.add_argument("--user", help="Studio user profile (default: base stack output studio_user_profile_name)")
    parser.add_argument("--session-hours", type=float, default=12,
                        help="Studio session length in hours, 0.5 to 12 (default 12)")
    parser.add_argument("--print-only", action="store_true",
                        help="Print the URL instead of opening it (it expires in 5 minutes)")
    args = parser.parse_args()

    if not 0.5 <= args.session_hours <= 12:
        sys.exit("--session-hours must be between 0.5 and 12 (AWS limits: 1800-43200 seconds).")

    domain_id, user = args.domain_id, args.user
    if not (domain_id and user):
        outputs = base_stack_outputs()
        domain_id = domain_id or outputs.get("studio_domain_id")
        user = user or outputs.get("studio_user_profile_name")
    if not (domain_id and user):
        sys.exit("No domain ID / user profile found in base/ outputs — has `pulumi up` been run there?")

    sm = boto3.Session(profile_name=args.profile, region_name=args.region).client("sagemaker")
    url = sm.create_presigned_domain_url(
        DomainId=domain_id,
        UserProfileName=user,
        SessionExpirationDurationInSeconds=int(args.session_hours * 3600),
        ExpiresInSeconds=300,
    )["AuthorizedUrl"]

    if args.print_only:
        print(url)
        print("\n(One-time link: open within 5 minutes.)", file=sys.stderr)
        return

    print(f"Opening Studio for '{user}' on {domain_id} ({args.session_hours:g}h session)...")
    if not webbrowser.open(url):
        print(url)
        print("\nCouldn't launch a browser — open the link above within 5 minutes.")


if __name__ == "__main__":
    sys.exit(main())
