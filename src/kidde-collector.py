import asyncio
import json
import datetime
from pathlib import Path
import config
import logging
import time
import traceback
from kidde_homesafe import KiddeClient, KiddeClientAuthError
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import ASYNCHRONOUS
from dataclasses import asdict

# General log level
log_level = getattr(logging, config.LOG_LEVEL, "INFO")

# Basic logging configuration
logger = logging.getLogger("kidde_collector")
logger.setLevel(log_level)

# Clear existing handlers
if not logger.handlers:
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    stream_handler = logging.StreamHandler()
    stream_handler.setLevel(log_level)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

logger.info("Logger initialized with level: %s", config.LOG_LEVEL)


class KiddeCollector:
    def __init__(self, kidde_api, influxdb_writer):
        self.kidde_api = kidde_api
        self.influxdb_writer = influxdb_writer
        self.validate_config()
        self.create_directories_and_files()
        logger.info("KiddeCollector initialized.")

    def validate_config(self):
        required_vars = {
            "INFLUXDB_URL": config.INFLUXDB_URL,
            "INFLUXDB_TOKEN": config.INFLUXDB_TOKEN,
            "INFLUXDB_ORG": config.INFLUXDB_ORG,
            "INFLUXDB_BUCKET": config.INFLUXDB_BUCKET,
            "KIDDE_USERNAME": config.KIDDE_USERNAME,
            "KIDDE_PASSWORD": config.KIDDE_PASSWORD,
            "COOKIES_DIR": config.COOKIES_DIR,
            "API_DATA_FOLDER": config.API_DATA_FOLDER,
            "FETCH_INTERVAL_SECONDS": config.FETCH_INTERVAL_SECONDS,
        }

        missing_vars = [var for var, value in required_vars.items() if not value]
        if missing_vars:
            error_message = f"The following configuration variables are missing or empty: {', '.join(missing_vars)}"
            logger.error(error_message)
            raise ValueError(error_message)

        logger.debug("Configuration validated successfully.")

    def create_directories_and_files(self):
        folder_path = Path(config.API_DATA_FOLDER)
        cookies_dir_path = config.COOKIES_DIR

        try:
            folder_path.mkdir(parents=True, exist_ok=True)
            cookies_dir_path.mkdir(parents=True, exist_ok=True)
            logger.debug(f"Directories created: {folder_path}, {cookies_dir_path}")
        except Exception as e:
            logger.error(f"Error creating directories: {e}")
            raise RuntimeError(f"Error creating directories: {e}")

    async def main_loop(self):
        logger.debug(f"INFLUXDB_URL: {config.INFLUXDB_URL}")
        logger.debug(f"INFLUXDB_TOKEN: {config.INFLUXDB_TOKEN}")
        logger.debug(f"INFLUXDB_ORG: {config.INFLUXDB_ORG}")
        logger.debug(f"INFLUXDB_BUCKET: {config.INFLUXDB_BUCKET}")
        logger.debug(f"KIDDE_USERNAME: {config.KIDDE_USERNAME}")
        logger.debug(f"KIDDE_PASSWORD: {config.KIDDE_PASSWORD}")
        logger.debug(f"COOKIES_DIR: {config.COOKIES_DIR}")
        logger.debug(f"API_DATA_FOLDER: {config.API_DATA_FOLDER}")
        logger.debug(f"FETCH_INTERVAL_SECONDS: {config.FETCH_INTERVAL_SECONDS}")
        logger.debug(f"WRITE_API_DATA: {config.WRITE_API_DATA}")

        while True:
            start_time = time.time()
            logger.info("Starting processing cycle")

            try:
                client = await self.kidde_api.get_kidde_client()
                if client is None:
                    raise Exception("Failed to create KiddeClient")

                data = await client.get_data(get_devices=True, get_events=False)
                serializable_data = asdict(data)

                if config.WRITE_API_DATA:
                    current_date = datetime.datetime.now().strftime("%Y-%m-%d")
                    folder_path = Path(config.API_DATA_FOLDER)
                    json_file_name = folder_path / f"api_data_{current_date}.json"
                    folder_path.mkdir(parents=True, exist_ok=True)

                    with open(json_file_name, "a") as json_file:
                        json.dump(serializable_data, json_file, indent=4)
                        json_file.write("\n")
                    logger.debug(f"Data saved to {json_file_name}")

                await self.influxdb_writer.write_data_to_influxdb(data)

                logger.info(f"Processed {len(data.devices)} devices")

            except Exception as e:
                logger.error(f"An error occurred: {e}")
                logger.error(traceback.format_exc())

            end_time = time.time()
            elapsed_time = end_time - start_time
            logger.info(f"Processing cycle completed in {elapsed_time:.2f} seconds")

            sleep_duration = max(0, config.FETCH_INTERVAL_SECONDS - elapsed_time)
            logger.info(f"Sleeping for {sleep_duration:.2f} seconds until next cycle")

            await asyncio.sleep(sleep_duration)


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
                client = await KiddeClient.from_login(config.KIDDE_USERNAME, config.KIDDE_PASSWORD)
                self.save_cookies(client.cookies)
            logger.debug("KiddeClient initialized successfully")
            return client
        except KiddeClientAuthError as auth_error:
            logger.error("Failed to initialize KiddeClient")
            logger.error(f"Exception type: {type(auth_error).__name__}")
            logger.error(f"Exception message: {auth_error}")
            logger.debug(traceback.format_exc())
            return None
        except Exception as e:
            logger.error("An error occurred while initializing KiddeClient")
            logger.error(f"Exception type: {type(e).__name__}")
            logger.error(f"Exception message: {e}")
            logger.debug(traceback.format_exc())
            return None

    def load_cookies(self):
        if self.cookies_file_path.exists():
            logger.debug(f"Loading cookies from {self.cookies_file_path}")
            with self.cookies_file_path.open("r") as file:
                cookies = json.load(file)
                logger.debug("Cookies loaded successfully")
                return cookies
        logger.debug("No cookies file found")
        return None

    def save_cookies(self, cookies):
        logger.debug(f"Saving cookies to {self.cookies_file_path}")
        with self.cookies_file_path.open("w") as file:
            json.dump(cookies, file)
        logger.debug("Cookies saved successfully")


class InfluxDBWriter:
    NESTED_ITEMS = ["iaq_temperature", "humidity", "hpa", "tvoc", "iaq", "co2"]

    def __init__(self):
        logger.info("InfluxDBWriter initialized.")

    async def write_data_to_influxdb(self, data):
        influx_client = InfluxDBClient(
            url=config.INFLUXDB_URL,
            token=config.INFLUXDB_TOKEN,
            org=config.INFLUXDB_ORG,
        )
        write_api = influx_client.write_api(write_options=ASYNCHRONOUS)

        for device in data.devices.values():
            location_label = data.locations[device["location_id"]]["label"]
            await self.write_to_influxdb(write_api, device, location_label)

        influx_client.close()
        logger.debug("InfluxDB client closed")

    async def write_to_influxdb(self, write_api, device, location_label):
        logger.debug(
            f"Writing data to InfluxDB for device: {device['serial_number']} at location: {location_label}"
        )
        main_point = (
            Point("kidde_collector_device")
            .tag("id", str(device["id"]))
            .tag("serial_number", device["serial_number"])
            .tag("location_id", str(device["location_id"]))
            .tag("location_label", location_label)
            .tag("label", device["label"])
        )

        for key, value in device.items():
            if isinstance(value, (int, float, bool, str)):
                main_point = main_point.field(key, value)

        write_api.write(
            bucket=config.INFLUXDB_BUCKET, org=config.INFLUXDB_ORG, record=main_point
        )
        logger.debug(
            f"Main point written to InfluxDB for device: {device['serial_number']}"
        )

        for item in self.NESTED_ITEMS:
            if item in device:
                nested_point = (
                    Point("kidde_collector_device")
                    .tag("id", str(device["id"]))
                    .tag("serial_number", device["serial_number"])
                    .tag("location_id", str(device["location_id"]))
                    .tag("location_label", location_label)
                    .tag("label", device["label"])
                    .field(f"{item}_value", device[item]["value"])
                    .field(f"{item}_status", device[item]["status"])
                )
                write_api.write(
                    bucket=config.INFLUXDB_BUCKET,
                    org=config.INFLUXDB_ORG,
                    record=nested_point,
                )
                logger.debug(
                    f"Nested point for {item} written to InfluxDB for device: {device['serial_number']}"
                )


if __name__ == "__main__":
    try:
        kidde_api = KiddeAPI()
        influxdb_writer = InfluxDBWriter()
        collector = KiddeCollector(kidde_api, influxdb_writer)
        asyncio.run(collector.main_loop())
    except ValueError as e:
        logger.error(str(e))
        exit(1)
    except Exception as e:
        logger.error(str(e))
        logger.error(traceback.format_exc())
        exit(1)
