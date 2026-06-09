"""MCP server exposing Squadery worklog tools to Claude. Run directly for stdio
transport, or point Claude Desktop at it (see README)."""

from datetime import date as _date

from mcp.server.fastmcp import FastMCP

from squadery_client import (
    DEFAULT_CATEGORY,
    AlreadyLogged,
    NotAuthenticated,
    get_status,
    log_worklog,
)

mcp = FastMCP("squadery")


@mcp.tool()
async def log_work(hours: float, project: str = "", notes: str = "", date: str = "",
                   category: str = DEFAULT_CATEGORY, replace: bool = False) -> str:
    """Log hours to Squadery Connect for a day.

    Squadery allows one worklog per day+squad. By default this refuses if the day
    is already logged; pass replace=true to overwrite it with this single entry
    (e.g. "set today to 9 hours").

    Args:
        hours: Hours worked.
        project: Squad/project name (case-insensitively matched; optional if you
            have a single squad).
        notes: Optional description.
        date: YYYY-MM-DD; defaults to today.
        category: Category in title case (e.g. "AI Development", "Meeting").
        replace: Overwrite an existing worklog for the day.
    """
    try:
        return await log_worklog(date or _date.today().isoformat(), hours, project,
                                 notes, category, replace)
    except (AlreadyLogged, NotAuthenticated) as e:
        return str(e)


@mcp.tool()
async def check_today(date: str = "") -> str:
    """Show what's currently logged for a day (default today)."""
    try:
        return await get_status(date or None)
    except NotAuthenticated as e:
        return str(e)


if __name__ == "__main__":
    mcp.run()
