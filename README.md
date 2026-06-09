# Squadery worklog automation

Log your daily hours on [Squadery Connect](https://connect.squadery.com) two ways:

- **From Claude** — an MCP server (`mcp_server.py`)
- **On a schedule** — a cron script (`scheduled_run.py`)

Both share one core module, `squadery_client.py`, which calls Squadery's GraphQL
API directly (`manage-api.squadery.com`).

## How auth works

You log in **once** through a real browser; the session is saved to
`~/.squadery/state.json` and reused headlessly afterwards. The app's AWS Cognito
token lives in that session — the client decrypts it and auto-refreshes it via
Cognito when it nears expiry, so unattended runs keep working. Your password is
never typed by the script, and Playwright is used **only** for this login.

## Setup

```bash
pip install -r requirements.txt
playwright install chromium

python auth_setup.py   # opens a browser; log in with Google, then press Enter
```

Re-run `auth_setup.py` if a run reports the session expired.

## Use from Claude

Add to `claude_desktop_config.json`, then restart Claude Desktop:

```json
{
  "mcpServers": {
    "squadery": {
      "command": "python",
      "args": ["/full/path/to/squadery-mcp/mcp_server.py"]
    }
  }
}
```

Tools:

- **`log_work(hours, project?, notes?, date?, category?, replace?)`** — log a
  day. Squadery allows one worklog per day+squad, so this refuses an
  already-logged day unless `replace=true`, which overwrites it with a single
  entry. Examples: *"log 8 hours today"*, *"set today to 9 hours"*.
- **`check_today(date?)`** — show what's logged for a day.

`project` is matched case-insensitively to your assigned squads (optional if you
have just one). `category` is a title-case Squadery category (default
`"AI Development"`).

## Use from cron

`scheduled_run.py` logs today's hours from environment variables:

```bash
# Weekdays at 6pm: log 8h, skip if already logged
0 18 * * 1-5 SQUADERY_HOURS=8 SQUADERY_PROJECT="Project Atlas" \
  SQUADERY_NOTES="Development work" \
  /usr/bin/python /full/path/to/squadery-mcp/scheduled_run.py >> /tmp/squadery.log 2>&1
```

Env vars: `SQUADERY_HOURS`, `SQUADERY_PROJECT`, `SQUADERY_NOTES`,
`SQUADERY_CATEGORY`, `SQUADERY_REPLACE` (`1`/`true` to overwrite), and
`SQUADERY_STATE` (session file path). On an already-logged day it prints `SKIP`;
on an expired session it exits non-zero with `AUTH FAILED` — wire that to an
alert so you know to re-run `auth_setup.py`.

## Backfill a date range from git history

`backfill.py` fills in past development work: for each working day (Mon–Fri) in a
range it appends one task sized to top the day up to a target (default 9h), with
a description derived from that day's commits in a repo. Existing entries (e.g.
meetings) are left untouched; days already at the target are skipped. **Dry-run
by default** — add `--commit` to write.

```bash
python backfill.py --start 2026-06-01 --end 2026-06-09        # preview
python backfill.py --start 2026-06-01 --end 2026-06-09 --commit
python backfill.py --month 2026-06 --commit                   # whole month (capped at today)
```

Options: `--repo`, `--author` (git author to summarise), `--project`,
`--category`, `--target-hours`, `--day-start`/`--fri-start` (start hours, UTC),
`--max-subjects` (commit lines per description), `--fallback` (text for days with
no commits). Reuses `add_worklog_task` from `squadery_client.py`.

## Note

This automates your employer's SSO-protected system. Check your IT/security
policy on storing session tokens and automating logins before relying on it.
