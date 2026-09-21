"""
Networking — uses the account's default VPC and its subnets.

The book's SageMaker Studio setup (Chapter 1) just needs a VPC + subnets to
attach Studio's network interfaces to. The default VPC that every new AWS
account ships with is fine for this — no need to build a custom VPC just to
follow along with the book.
"""

import pulumi_aws as aws


def get_default_network():
    """Return (vpc_id, subnet_ids) for the account's default VPC."""
    default_vpc = aws.ec2.get_vpc(default=True)
    subnets = aws.ec2.get_subnets(
        filters=[
            aws.ec2.GetSubnetsFilterArgs(
                name="vpc-id",
                values=[default_vpc.id],
            )
        ]
    )
    return default_vpc.id, subnets.ids
