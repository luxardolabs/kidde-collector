import os
from pathlib import Path

# InfluxDB credentials and details
INFLUXDB_URL = os.getenv("KIDDE_COLLECTOR_INFLUXDB_URL")
INFLUXDB_TOKEN = os.getenv("KIDDE_COLLECTOR_INFLUXDB_TOKEN")
INFLUXDB_ORG = os.getenv("KIDDE_COLLECTOR_INFLUXDB_ORG")
INFLUXDB_BUCKET = os.getenv("KIDDE_COLLECTOR_INFLUXDB_BUCKET")

# Kidde Client credentials
KIDDE_USERNAME = os.getenv("KIDDE_COLLECTOR_KIDDE_USERNAME")
KIDDE_PASSWORD = os.getenv("KIDDE_COLLECTOR_KIDDE_PASSWORD")

# Path for cookies file
COOKIES_DIR = Path(os.getenv("KIDDE_COLLECTOR_COOKIES_DIR", "cookies"))

# Directory for saving API data
API_DATA_FOLDER = os.getenv("KIDDE_COLLECTOR_API_DATA_FOLDER", "export")

# Interval in seconds
FETCH_INTERVAL_SECONDS = int(os.getenv("KIDDE_COLLECTOR_FETCH_INTERVAL_SECONDS", "60"))

# Log level
LOG_LEVEL = os.getenv("KIDDE_COLLECTOR_LOG_LEVEL", "INFO").upper()

# Flag to write API data
WRITE_API_DATA = os.getenv("KIDDE_COLLECTOR_WRITE_API_DATA", "true").lower() == "true"
