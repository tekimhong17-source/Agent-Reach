"""Set a customer's plan by hand — for rescuing a sale the webhook missed.

Webhooks are not guaranteed. If Gumroad's webhook was pointing at an old
deployment when someone paid, the ping never arrived and there is nothing to
retry: that customer is charged and still on the free plan. This is the
one-line fix.

    python -m backend.grant --list                      # who exists, and on what plan
    python -m backend.grant buyer@example.com pro       # rescue a subscription sale
    python -m backend.grant buyer@example.com lifetime  # rescue a one-time sale
    python -m backend.grant buyer@example.com free      # revoke

On Railway, run it against the live database with `railway run` (or from the
service shell) so CARDVAULT_DB points at the mounted volume.

If you are rescuing a *subscription* sale, pass the Gumroad subscription id:

    python -m backend.grant buyer@example.com pro --subscription-id 12345

Without it the account is upgraded but never linked to the subscription, so
when the customer eventually cancels, the `subscription_ended` webhook cannot
find them and they keep Pro forever.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from . import database

PLANS = ("free", "pro", "lifetime")


def _fmt_time(ts: float) -> str:
    try:
        return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        return "?"


def _print_users(users: list[dict]) -> None:
    if not users:
        print("No accounts yet.")
        return
    print(f"{'PLAN':<9} {'CARDS':>5}  {'JOINED':<11} {'SUBSCRIPTION':<14} EMAIL")
    for u in users:
        print(
            f"{u['plan']:<9} {u['cards']:>5}  {_fmt_time(u['created_at']):<11} "
            f"{(u['subscription_id'] or '-'):<14} {u['email']}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m backend.grant",
        description="Set a customer's plan by hand (rescue a missed webhook).",
    )
    parser.add_argument("email", nargs="?", help="the customer's account email")
    parser.add_argument("plan", nargs="?", choices=PLANS, help="plan to set")
    parser.add_argument(
        "--subscription-id",
        help="Gumroad subscription id — required for a subscription sale, or "
        "cancellation can never downgrade this account",
    )
    parser.add_argument("--list", action="store_true", help="list accounts and exit")
    parser.add_argument("-y", "--yes", action="store_true", help="skip the confirmation")
    args = parser.parse_args(argv)

    database.init_db()

    if args.list:
        _print_users(database.recent_users())
        return 0

    if not args.email or not args.plan:
        parser.error("give an EMAIL and a PLAN, or use --list")

    user = database.get_user_by_email(args.email)
    if not user:
        print(f"No account with the email {args.email!r}.", file=sys.stderr)
        print("Run --list to see existing accounts. The buyer's Gumroad email "
              "and their CardVault email can differ.", file=sys.stderr)
        return 1

    if user["plan"] == args.plan and (
        args.subscription_id is None or user["subscription_id"] == args.subscription_id
    ):
        print(f"{user['email']} is already on {args.plan} — nothing to do.")
        return 0

    print(f"  account:      {user['email']}")
    print(f"  plan:         {user['plan']}  ->  {args.plan}")
    if args.subscription_id:
        print(f"  subscription: {user['subscription_id'] or '-'}  ->  {args.subscription_id}")
    elif args.plan == "pro":
        print("  subscription: not set — cancellation will NOT downgrade this "
              "account. Pass --subscription-id if this was a subscription sale.")

    if not args.yes:
        try:
            if input("Apply? [y/N] ").strip().lower() not in ("y", "yes"):
                print("Cancelled.")
                return 1
        except EOFError:
            print("Not an interactive terminal — re-run with --yes.", file=sys.stderr)
            return 1

    database.set_plan(user["id"], args.plan, subscription_id=args.subscription_id)
    updated = database.get_user(user["id"])
    print(f"Done. {updated['email']} is now on {updated['plan']}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
