import logging
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import ASYNCHRONOUS
import config

logger = logging.getLogger("kidde_collector")


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
