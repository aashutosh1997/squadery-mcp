"""Backfill Squadery development worklogs for a date range.

Appends one task per working day (Mon-Fri), sized to top each day up to a target
(default 9h), with a description derived from that day's git commits in a repo.
Existing entries (e.g. meetings) are left untouched; days already at the target
are skipped. Dry-run by default.

    python backfill.py --start 2026-06-01 --end 2026-06-09        # preview
    python backfill.py --start 2026-06-01 --end 2026-06-09 --commit
    python backfill.py --month 2026-06 --commit                   # whole month (capped at today)
"""

import argparse
import asyncio
import subprocess
from datetime import date, datetime, timedelta, timezone

import squadery_client as sc

DEFAULT_REPO = "/Users/aashutosh.py/Desktop/navigation"
DEFAULT_AUTHOR = "aashutosh.pyakurel@fusemachines.com"
FALLBACK_DESC = "Development work on the navigation project."


def date_range(args) -> tuple[date, date]:
    """Resolve --month or --start/--end into an inclusive range, capped at today."""
    if args.month:
        y, m = map(int, args.month.split("-"))
        start = date(y, m, 1)
        end = date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)
    else:
        start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    today = datetime.now(timezone.utc).date()
    return start, min(end, today)


def commits_by_day(repo: str, author: str, start: date, end: date) -> dict[str, list[str]]:
    """Map each date in [start, end] to that day's commit subjects (by author date,
    deduped, merges dropped). Window is widened so commit-date filtering can't miss
    commits whose author date is in range."""
    out = subprocess.run(
        ["git", "-C", repo, "log", "--all", f"--author={author}", "--date=short",
         f"--since={(start - timedelta(days=4)).isoformat()}",
         f"--until={(end + timedelta(days=5)).isoformat()}", "--pretty=%ad%x09%s"],
        capture_output=True, text=True).stdout
    by: dict[str, list[str]] = {}
    seen: set[tuple[str, str]] = set()
    lo, hi = start.isoformat(), end.isoformat()
    for line in out.splitlines():
        if "\t" not in line:
            continue
        day, subject = line.split("\t", 1)
        subject = subject.strip()
        if not (lo <= day <= hi) or subject.startswith("Merge ") or (day, subject) in seen:
            continue
        seen.add((day, subject))
        by.setdefault(day, []).append(subject)
    return by


def describe(subjects: list[str] | None, fallback: str, max_subjects: int) -> str:
    if not subjects:
        return fallback
    desc = "; ".join(subjects[:max_subjects])
    if len(subjects) > max_subjects:
        desc += f"; (+{len(subjects) - max_subjects} more)"
    return desc


async def main(args) -> None:
    start, end = date_range(args)
    target = round(args.target_hours * 60)
    uid = await sc._user_id()
    sid, squad = await sc._resolve_squad(args.project, uid)
    commits = commits_by_day(args.repo, args.author, start, end)
    print(f"squad: {squad}  |  range: {start}..{end}  |  target: {target}m  |  "
          f"mode: {'COMMIT' if args.commit else 'DRY-RUN'}\n")
    print(f"{'date':11} {'day':3} {'have':>5} {'add':>5}  {'window':12} description")

    total = 0
    day = start
    while day <= end:
        if day.weekday() < 5:                       # Mon-Fri
            iso = day.isoformat()
            wl = await sc._day_worklog(uid, sid, iso)
            have = sum(t["minutes"] for t in (wl or {}).get("worklogtasks") or [])
            dev = target - have
            if dev <= 0:
                print(f"{iso} {day.strftime('%a')} {have:5} {'skip':>5}  already ≥ {target}m")
            else:
                hour = args.fri_start if day.weekday() == 4 else args.day_start
                s = datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc)
                e = s + timedelta(minutes=dev)
                fmt = lambda x: x.strftime("%Y-%m-%dT%H:%M:00.000Z")
                desc = describe(commits.get(iso), args.fallback, args.max_subjects)
                total += dev
                print(f"{iso} {day.strftime('%a')} {have:5} {dev:5}  "
                      f"{s.strftime('%H:%M')}-{e.strftime('%H:%M')}Z  {desc[:70]}")
                if args.commit:
                    print("   ->", await sc.add_worklog_task(
                        iso, dev, args.project, desc, args.category, fmt(s), fmt(e)))
        day += timedelta(days=1)

    print(f"\n{'COMMITTED' if args.commit else 'DRY-RUN'}: total dev minutes "
          f"{'added' if args.commit else 'to add'} = {total} ({total / 60:.1f}h)")
    if not args.commit:
        print("Re-run with --commit to apply.")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Backfill Squadery dev worklogs from git history.")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--month", help="YYYY-MM (whole month, capped at today)")
    g.add_argument("--start", help="YYYY-MM-DD (use with --end)")
    p.add_argument("--end", help="YYYY-MM-DD inclusive")
    p.add_argument("--repo", default=DEFAULT_REPO)
    p.add_argument("--author", default=DEFAULT_AUTHOR)
    p.add_argument("--project", default="RDS")
    p.add_argument("--category", default=sc.DEFAULT_CATEGORY)
    p.add_argument("--target-hours", type=float, default=9.0)
    p.add_argument("--day-start", type=int, default=13, help="Mon-Thu start hour UTC (default 13)")
    p.add_argument("--fri-start", type=int, default=9, help="Fri start hour UTC (default 9)")
    p.add_argument("--max-subjects", type=int, default=6, help="commit subjects per description")
    p.add_argument("--fallback", default=FALLBACK_DESC, help="description for no-commit days")
    p.add_argument("--commit", action="store_true", help="write to Squadery (default: dry-run)")
    a = p.parse_args()
    if a.start and not a.end:
        p.error("--start requires --end")
    asyncio.run(main(a))
