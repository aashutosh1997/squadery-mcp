"""Cron interface: log (or replace) today's worklog unattended. Configure via
env vars — see the crontab example in the README."""

import asyncio
import os
from datetime import date

from squadery_client import AlreadyLogged, NotAuthenticated, log_worklog

HOURS = float(os.environ.get("SQUADERY_HOURS", "8"))
PROJECT = os.environ.get("SQUADERY_PROJECT", "")
NOTES = os.environ.get("SQUADERY_NOTES", "")
REPLACE = os.environ.get("SQUADERY_REPLACE", "").lower() in ("1", "true", "yes")


async def main() -> None:
    try:
        print(await log_worklog(date.today().isoformat(), HOURS, PROJECT, NOTES, replace=REPLACE))
    except AlreadyLogged as e:
        print(f"SKIP: {e}")
    except NotAuthenticated as e:
        # Cron can't log in interactively, so fail loudly (wire to an alert).
        print(f"AUTH FAILED: {e}")
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
