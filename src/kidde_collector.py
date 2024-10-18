import asyncio
import logging
import time
import datetime
import json
from pathlib import Path
from dataclasses import asdict
import config
from kidde_api import KiddeAPI
from influxdb_writer import InfluxDBWriter
import traceback

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
                logger.debug(traceback.format_exc())

            end_time = time.time()
            elapsed_time = end_time - start_time
            logger.info(f"Processing cycle completed in {elapsed_time:.2f} seconds")

            sleep_duration = max(0, config.FETCH_INTERVAL_SECONDS - elapsed_time)
            logger.info(f"Sleeping for {sleep_duration:.2f} seconds until next cycle")

            await asyncio.sleep(sleep_duration)


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
