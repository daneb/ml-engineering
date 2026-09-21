"""
Cost guardrail #2 — an AWS Budget with email alerts.

This is a detective control, not a preventive one: it won't stop a big
instance from launching (guardrails.py does that), but it's the thing that
would have told you *the same day* that spend was climbing toward the $100
ceiling, instead of finding out a day later when the credits were gone.

Alerts fire at 50%, 80%, and 100% of actual spend, plus a forecasted-to-
exceed-100% warning so you get a heads-up before the month even ends.
"""

import pulumi_aws as aws


def create_cost_budget(
    name_prefix: str,
    monthly_limit_usd: str,
    alert_email: str,
) -> aws.budgets.Budget:
    thresholds = [50, 80, 100]

    notifications = [
        aws.budgets.BudgetNotificationArgs(
            comparison_operator="GREATER_THAN",
            threshold=threshold,
            threshold_type="PERCENTAGE",
            notification_type="ACTUAL",
            subscriber_email_addresses=[alert_email],
        )
        for threshold in thresholds
    ]
    notifications.append(
        aws.budgets.BudgetNotificationArgs(
            comparison_operator="GREATER_THAN",
            threshold=100,
            threshold_type="PERCENTAGE",
            notification_type="FORECASTED",
            subscriber_email_addresses=[alert_email],
        )
    )

    return aws.budgets.Budget(
        f"{name_prefix}-monthly-budget",
        name=f"{name_prefix}-monthly-budget",
        budget_type="COST",
        limit_amount=monthly_limit_usd,
        limit_unit="USD",
        time_unit="MONTHLY",
        time_period_start="2025-01-01_00:00",
        time_period_end="2087-06-15_00:00",
        notifications=notifications,
    )
