"""Squadery Connect worklog client (GraphQL API at manage-api.squadery.com).

Auth uses the AWS Cognito token from your saved browser session
(~/.squadery/state.json, created once by auth_setup.py). The web app stores the
token AES-GCM-encrypted via flutter_secure_storage; we decrypt it and refresh it
through Cognito when it nears expiry. Playwright is used only for the login.
"""

from __future__ import annotations

import base64
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

GRAPHQL_URL = "https://manage-api.squadery.com/graphql"
ORIGIN = "https://connect.squadery.com"
CLIENT_ID = "1cavkpa6at2fi0kkgjbm7v7808"
COGNITO_URL = "https://cognito-idp.us-east-1.amazonaws.com/"
STATE_FILE = Path(os.environ.get("SQUADERY_STATE", "~/.squadery/state.json")).expanduser()
DEFAULT_CATEGORY = os.environ.get("SQUADERY_CATEGORY", "AI Development")

_SS = f"FlutterSecureStorage.{CLIENT_ID}.hostedUi"
_REAUTH = "Re-run `python auth_setup.py`."


class NotAuthenticated(Exception):
    """No valid saved session / token."""


class AlreadyLogged(Exception):
    """The day already has a worklog and replace was not requested."""


# --- auth ---------------------------------------------------------------------
def _decrypt(value: str, key: bytes) -> str:
    """Decrypt a flutter_secure_storage value: base64(iv).base64(ciphertext+tag)."""
    iv, ct = value.split(".", 1)
    return AESGCM(key).decrypt(base64.b64decode(iv), base64.b64decode(ct), None).decode()


async def _access_token() -> str:
    """Decrypt the saved Cognito token, refreshing via Cognito if near expiry."""
    if not STATE_FILE.exists():
        raise NotAuthenticated(f"No saved session at {STATE_FILE}. {_REAUTH}")
    store = {
        item["name"]: item["value"]
        for origin in json.loads(STATE_FILE.read_text()).get("origins", [])
        if "squadery.com" in origin.get("origin", "")
        for item in origin.get("localStorage", [])
    }
    try:
        key = base64.b64decode(store["FlutterSecureStorage"])
        access = _decrypt(store[f"{_SS}.accessToken"], key)
        refresh = _decrypt(store[f"{_SS}.refreshToken"], key)
    except (KeyError, ValueError) as exc:
        raise NotAuthenticated(f"Saved session is unreadable ({exc}). {_REAUTH}")

    claims = access.split(".")[1]
    exp = json.loads(base64.urlsafe_b64decode(claims + "=" * (-len(claims) % 4)))["exp"]
    if exp - time.time() > 120:
        return access

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(COGNITO_URL, json={
            "AuthFlow": "REFRESH_TOKEN_AUTH", "ClientId": CLIENT_ID,
            "AuthParameters": {"REFRESH_TOKEN": refresh}}, headers={
            "Content-Type": "application/x-amz-json-1.1",
            "X-Amz-Target": "AWSCognitoIdentityProviderService.InitiateAuth"})
    if resp.status_code != 200:
        raise NotAuthenticated(f"Session expired and refresh failed. {_REAUTH}")
    return resp.json()["AuthenticationResult"]["AccessToken"]


async def _graphql(query: str, variables: dict | None = None) -> dict:
    """Run one GraphQL operation; return its `data` or raise on errors."""
    token = await _access_token()
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(GRAPHQL_URL, json={"query": query, "variables": variables or {}},
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json", "Origin": ORIGIN})
    if resp.status_code in (401, 403):
        raise NotAuthenticated(f"Session rejected by server. {_REAUTH}")
    body = resp.json()
    if body.get("errors"):
        raise RuntimeError("; ".join(e.get("message", "?") for e in body["errors"]))
    return body["data"]


# --- lookups ------------------------------------------------------------------
async def _user_id() -> str:
    return (await _graphql("{ getUserInfo { id } }"))["getUserInfo"]["id"]


async def _resolve_squad(project: str, user_id: str) -> tuple[str, str]:
    """Match `project` (case-insensitive substring) to (squadId, squadName)."""
    squads = (await _graphql(
        "query($u:String!){getUserAssignedSquadsByUserId(userId:$u){squadId squadName}}",
        {"u": user_id}))["getUserAssignedSquadsByUserId"] or []
    needle = project.strip().lower()
    for squad in squads:
        if needle and needle in squad["squadName"].lower():
            return squad["squadId"], squad["squadName"]
    if len(squads) == 1:
        return squads[0]["squadId"], squads[0]["squadName"]
    raise RuntimeError(f"No squad matched '{project}'. Available: "
                       + ", ".join(s["squadName"] for s in squads))


# getWorklogRequests is the web app's "WorklogList": one worklog per day+squad
# with its tasks. Pages are 1-indexed and the filter needs fromDate == toDate ==
# midnight; page 0 or a date range makes the backend return null content.
_WORKLOG_LIST = """query($p:PageRequest!,$f:WorklogFilter!){
  getWorklogRequests(pageRequest:$p,worklogFilter:$f){
    content{ id worklogtasks{ id minutes category } } } }"""


async def _day_worklog(user_id: str, squad_id: str, date: str) -> dict | None:
    """The existing worklog (with tasks) for date+squad, or None."""
    midnight = f"{date}T00:00:00.000Z"
    data = await _graphql(_WORKLOG_LIST, {
        "p": {"page": 1, "size": 50},
        "f": {"fromDate": midnight, "toDate": midnight, "includeDrafts": True,
              "squadId": squad_id, "userId": user_id}})
    content = (data.get("getWorklogRequests") or {}).get("content") or []
    return content[0] if content else None


def _window(date: str, minutes: int) -> tuple[str, str]:
    """ISO-Z start/end for a task: 09:00 UTC start, end = start + minutes."""
    start = datetime.fromisoformat(date).replace(tzinfo=timezone.utc, hour=9)
    iso = lambda d: d.isoformat().replace("+00:00", ".000Z")
    return iso(start), iso(start + timedelta(minutes=minutes))


# --- public API ---------------------------------------------------------------
async def log_worklog(date: str, hours: float, project: str = "", notes: str = "",
                      category: str = DEFAULT_CATEGORY, replace: bool = False) -> str:
    """Log `hours` for a day (YYYY-MM-DD). Squadery allows one worklog per
    day+squad: if one exists, raise AlreadyLogged unless `replace` is set, in
    which case its tasks are swapped for this single entry (the new task is added
    before the old ones are deleted, so the worklog is never momentarily empty).
    """
    user_id = await _user_id()
    squad_id, squad_name = await _resolve_squad(project, user_id)
    minutes = round(hours * 60)
    start, end = _window(date, minutes)
    task = {"category": category, "description": notes or f"{hours}h on {squad_name}",
            "minutes": minutes, "startTime": start, "endTime": end, "squadId": squad_id}
    existing = await _day_worklog(user_id, squad_id, date)

    if existing is None:
        await _graphql("mutation($w:NewWorklogDto!){createWorklog(worklogInput:$w){id}}",
            {"w": {"squadId": squad_id, "startDate": start, "endDate": end, "tasks": [task]}})
        return f"Logged {hours}h of {category} on '{squad_name}' for {date}."

    old = existing.get("worklogtasks") or []
    if not replace:
        raise AlreadyLogged(f"{squad_name} already has a worklog for {date} "
                            f"({sum(t['minutes'] for t in old)}m). Pass replace=true to overwrite.")
    await _graphql("mutation($t:NewWorklogTaskDto!){addWorklogTask(worklogTaskInput:$t){id}}",
        {"t": {**task, "worklogId": existing["id"]}})
    for old_task in old:
        await _graphql("mutation($id:String!){deleteWorklogTask(taskId:$id){message}}",
            {"id": old_task["id"]})
    return (f"Replaced {date}: removed {len(old)} task(s), "
            f"left {hours}h of {category} on '{squad_name}'.")


async def get_status(date: str | None = None) -> str:
    """What's actually logged for `date` (default today), summed per task.

    Reads the worklog list (what the web app shows), not the getWorklogOverview
    summary — the latter double-counts recurring entries and lags edits.
    """
    day = date or datetime.now(timezone.utc).date().isoformat()
    user_id = await _user_id()
    squads = (await _graphql(
        "query($u:String!){getUserAssignedSquadsByUserId(userId:$u){squadId squadName}}",
        {"u": user_id}))["getUserAssignedSquadsByUserId"] or []
    lines, total = [], 0
    for squad in squads:
        tasks = ((await _day_worklog(user_id, squad["squadId"], day)) or {}).get("worklogtasks") or []
        minutes = sum(t["minutes"] for t in tasks)
        if minutes:
            total += minutes
            detail = ", ".join(f"{t['category']} {t['minutes']}m" for t in tasks)
            lines.append(f"  {squad['squadName']}: {minutes}m ({detail})")
    if not total:
        return f"Nothing logged for {day}."
    return "\n".join([f"Logged {total}m ({total / 60:.2f}h) for {day}:", *lines])


async def save_session() -> None:
    """Open a browser for a one-time manual Google login, then save the session.
    Your password is never typed by the script."""
    from playwright.async_api import async_playwright

    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False, channel="chrome")
        context = await browser.new_context()
        await (await context.new_page()).goto(ORIGIN)
        print("Log in with your Google Workspace account in the browser window.")
        input("Once you see your Squadery dashboard, press Enter to save... ")
        await context.storage_state(path=str(STATE_FILE))
        await browser.close()
    print(f"Session saved to {STATE_FILE}")
