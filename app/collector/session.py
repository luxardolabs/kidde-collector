"""Kidde session management — cookie persistence + client creation.

The Kidde API is cookie-session based: log in once, persist the cookies to the
bind-mounted output dir, and reuse them across restarts. On a 401/403 (expired/invalid
session) the poller drops the cookie file so the next cycle re-authenticates.
"""

import json

import aiofiles
import aiohttp

from app.collector.client import KiddeClient, KiddeClientAuthError
from app.core import config
from app.utils.logging import logger


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
        """Return an authenticated client, logging in + caching cookies if needed."""
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
                raise KiddeClientAuthError("Kidde username/password not configured")
            client = await KiddeClient.from_login(
                self._http, config.KIDDE_USERNAME, config.KIDDE_PASSWORD
            )
            await self.save_cookies(client.cookies)
            logger.debug("KiddeClient authenticated successfully")
            return client
        except KiddeClientAuthError as auth_error:
            logger.error("Kidde authentication error: %s", auth_error)
            return None
        except aiohttp.ClientResponseError as e:
            if e.status == 401:
                logger.error("Unauthorized (401): check Kidde credentials.")
            else:
                logger.error("HTTP error %s - %s", e.status, e.message)
            return None
        except Exception as e:
            logger.error("Failed to create Kidde client: %s", e)
            return None

    async def load_cookies(self) -> dict[str, str] | None:
        if not self.cookies_file_path.exists():
            logger.debug("No cookies file found")
            return None
        logger.debug("Loading cookies from %s", self.cookies_file_path)
        async with aiofiles.open(self.cookies_file_path) as file:
            cookies: dict[str, str] = json.loads(await file.read())
            return cookies

    async def save_cookies(self, cookies: dict[str, str]) -> None:
        logger.debug("Saving cookies to %s", self.cookies_file_path)
        async with aiofiles.open(self.cookies_file_path, "w") as file:
            await file.write(json.dumps(cookies))
        # The session cookie is a live bearer credential — keep it owner-only on the
        # bind-mounted output dir.
        self.cookies_file_path.chmod(0o600)

    def invalidate(self) -> None:
        """Drop the cached cookie file so the next cycle re-authenticates."""
        try:
            self.cookies_file_path.unlink(missing_ok=True)
            logger.info("Cleared cached Kidde cookies; will re-login next cycle")
        except Exception as e:
            logger.error("Failed to clear cookies file: %s", e)
