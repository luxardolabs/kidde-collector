"""Kidde session management — cookie persistence + client creation.

The Kidde API is cookie-session based: log in once, persist the cookies to the
bind-mounted output dir, and reuse them across restarts. On a 401/403 (expired/invalid
session) the poller drops the cookie file so the next cycle re-authenticates.
"""

import asyncio
import json
import logging
import os

import aiofiles
import aiohttp

from app.collector.client import (
    KiddeClient,
    KiddeClientAuthError,
    KiddeLoginRejectedError,
)
from app.core import config

logger = logging.getLogger(__name__)


class KiddeSession:
    """Loads/persists Kidde session cookies and hands back a ready ``KiddeClient``."""

    def __init__(self) -> None:
        self.cookies_file_path = config.COOKIES_DIR / "cookies.json"
        config.COOKIES_DIR.mkdir(parents=True, exist_ok=True)
        self._http: aiohttp.ClientSession | None = None
        logger.info("KiddeSession initialized (cookies: %s)", self.cookies_file_path)

    async def connect(self) -> None:
        """Open the shared HTTP session — one long-lived connection pool for the process."""
        timeout = aiohttp.ClientTimeout(
            total=config.REQUEST_TIMEOUT, connect=config.CONNECTION_TIMEOUT
        )
        # The Kidde session cookie is persisted/replayed manually (survives restarts), so
        # disable aiohttp's own jar and pass cookies explicitly per request.
        self._http = aiohttp.ClientSession(
            timeout=timeout, cookie_jar=aiohttp.DummyCookieJar()
        )

    async def close(self) -> None:
        """Close the shared HTTP session on shutdown."""
        if self._http is not None:
            await self._http.close()
            self._http = None

    async def get_client(self) -> KiddeClient | None:
        """Return an authenticated client, logging in + caching cookies if needed.

        Raises KiddeLoginRejectedError when Kidde refuses the configured credentials, so the
        poller can back off rather than fail a login every cycle.
        """
        if self._http is None:
            raise RuntimeError(
                "KiddeSession.connect() must be called before get_client()"
            )
        try:
            cookies = await self.load_cookies()
            if cookies:
                logger.debug("Loaded cached cookies for KiddeClient")
                return KiddeClient(self._http, cookies)

            logger.debug("No cached cookies, logging in to Kidde")
            if not config.KIDDE_USERNAME or not config.KIDDE_PASSWORD:
                raise KiddeLoginRejectedError("Kidde username/password not configured")
            try:
                client = await KiddeClient.from_login(
                    self._http, config.KIDDE_USERNAME, config.KIDDE_PASSWORD
                )
            except KiddeClientAuthError as rejected:
                raise KiddeLoginRejectedError(
                    str(rejected) or "Kidde rejected the configured credentials"
                ) from rejected
            await self.save_cookies(client.cookies)
            logger.debug("KiddeClient authenticated successfully")
            return client
        except KiddeLoginRejectedError:
            raise
        except aiohttp.ClientResponseError as e:
            if e.status == 401:
                logger.exception("Unauthorized (401): check Kidde credentials.")
            else:
                logger.exception("HTTP error %s - %s", e.status, e.message)
            return None
        # swallowed-exceptions: a failed login must not kill the collector. Returning None
        # is the handled outcome — the caller skips this cycle and the poll loop retries on
        # the next interval (the documented resilience contract). The cause is logged at
        # error level with its traceback, so the failure is recorded, not lost.
        except Exception as e:
            logger.exception("Failed to create Kidde client: %s", e)
            return None

    async def load_cookies(self) -> dict[str, str] | None:
        # .exists() is a blocking stat() — off the loop (blocking-io sweep).
        if not await asyncio.to_thread(self.cookies_file_path.exists):
            logger.debug("No cookies file found")
            return None
        logger.debug("Loading cookies from %s", self.cookies_file_path)
        # swallowed-exceptions: a cache this process cannot read (OSError: another uid's or
        # a 0000 file) or cannot parse (ValueError: bad UTF-8 or bad JSON) is dropped below
        # so this cycle logs in fresh. It sits on a persistent volume, so dropping it is the
        # recovery: a restart keeps it.
        try:
            # blocking-io: aiofiles.open is the ASYNC file API, not pathlib.Path.open — the
            # read is awaited and never touches the loop's thread. Sweep matches on the
            # method name; the receiver here is the aiofiles module.
            async with aiofiles.open(self.cookies_file_path, "rb") as file:
                cookies = json.loads(await file.read())
        except OSError, ValueError:
            cookies = None
        if not isinstance(cookies, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in cookies.items()
        ):
            logger.warning(
                "Cookie cache %s is unreadable; dropping it and logging in fresh",
                self.cookies_file_path,
            )
            self.invalidate()
            return None
        return cookies

    async def save_cookies(self, cookies: dict[str, str]) -> None:
        logger.debug("Saving cookies to %s", self.cookies_file_path)
        await asyncio.to_thread(self._write_owner_only, json.dumps(cookies))

    def _write_owner_only(self, text: str) -> None:
        """Write the cookie file 0600 from creation, then rename it into place.

        The session cookie is a live bearer credential on a bind-mounted dir. Opened 0600, it
        is never readable by others, even on a mount where chmod fails (CIFS/NFS); the atomic
        rename means a crash mid-write leaves the previous file, never a truncated one.
        """
        tmp = self.cookies_file_path.with_name(self.cookies_file_path.name + ".tmp")
        tmp.unlink(missing_ok=True)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as file:
            file.write(text)
        tmp.replace(self.cookies_file_path)

    def invalidate(self) -> None:
        """Drop the cached cookie file so the next cycle re-authenticates."""
        try:
            self.cookies_file_path.unlink(missing_ok=True)
            logger.info("Cleared cached Kidde cookies; will re-login next cycle")
        # swallowed-exceptions: best-effort cleanup. Failing to delete a cached cookie file
        # (read-only mount, permissions) must not abort the re-auth path it exists to enable
        # — the next login simply overwrites it. Recorded at error level with its traceback.
        except Exception as e:
            logger.exception("Failed to clear cookies file: %s", e)
