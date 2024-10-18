import json
import logging
from pathlib import Path
import config
from kidde_homesafe import KiddeClient, KiddeClientAuthError
import aiohttp

logger = logging.getLogger("kidde_collector")


class KiddeAPI:
    def __init__(self):
        self.cookies_file_path = self.get_cookies_file_path()
        logger.info("KiddeAPI initialized.")

    def get_cookies_file_path(self):
        cookies_dir_path = config.COOKIES_DIR
        cookies_file_path = cookies_dir_path / "cookies.json"
        logger.debug(f"Cookies file path: {cookies_file_path}")
        return cookies_file_path

    async def get_kidde_client(self):
        try:
            cookies = self.load_cookies()
            if cookies:
                logger.debug("Loaded cookies for KiddeClient")
                client = KiddeClient(cookies)
            else:
                logger.debug("No cookies found, logging in to KiddeClient")
                client = await KiddeClient.from_login(
                    config.KIDDE_USERNAME, config.KIDDE_PASSWORD
                )
                self.save_cookies(client.cookies)
            logger.debug("KiddeClient initialized successfully")
            return client
        except KiddeClientAuthError as auth_error:
            logger.error(
                f"Failed to initialize KiddeClient due to authorization error: {auth_error}"
            )
            return None
        except aiohttp.ClientResponseError as e:
            if e.status == 401:
                logger.error("Unauthorized access (401): Check credentials.")
                logger.error(f"URL: {e.request_info.url}")
            else:
                logger.error(f"HTTP Error {e.status} - {e.message}")
            return None
        except Exception as e:
            logger.error(f"An error occurred: {str(e)}")
            return None

    def load_cookies(self):
        if self.cookies_file_path.exists():
            logger.debug(f"Loading cookies from {self.cookies_file_path}")
            with self.cookies_file_path.open("r") as file:
                cookies = json.load(file)
                return cookies
        logger.debug("No cookies file found")
        return None

    def save_cookies(self, cookies):
        logger.debug(f"Saving cookies to {self.cookies_file_path}")
        with self.cookies_file_path.open("w") as file:
            json.dump(cookies, file)
        logger.debug("Cookies saved successfully")
