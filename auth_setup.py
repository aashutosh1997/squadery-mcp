"""One-time manual Google login that seeds the saved session. Re-run whenever it
expires. Your password is never typed by the script."""

import asyncio

from squadery_client import save_session

if __name__ == "__main__":
    asyncio.run(save_session())
