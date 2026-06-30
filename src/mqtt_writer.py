import logging
import config
from ha_mqtt_discoverable import Settings, DeviceInfo
from ha_mqtt_discoverable.sensors import BinarySensor, BinarySensorInfo, NumberInfo, Number, Text, TextInfo

logger = logging.getLogger("kidde_collector")

class MqttWriter:
    MODELS = ['waterleakdetector']

    def __init__(self):
        self.mqtt_settings = Settings.MQTT(host=config.MQTT_HOST, username=config.MQTT_USERNAME, password=config.MQTT_PASSWORD)
        logger.info("MQTT initialized.")

    async def write_data_to_mqtt(self, data):
        for device in data.devices.values():
            location_label = data.locations[device["location_id"]]["label"]
            await self.write_to_mqtt(self.mqtt_settings, device, location_label)

    async def write_to_mqtt(self, mqtt_settings, device, location_label):
        logger.debug(
            f"Writing data to MQTT for device: {device['serial_number']} at location: {location_label}"
        )

        device_info = DeviceInfo(name=device["label"], identifiers=[str(device["id"])])
        logger.debug(
            f"MQTT device created for device: {device['serial_number']}"
        )
        #TODO CERATE sensors for other device types
        def _noop_command_callback(*args, **kwargs):
            return None

        match device['model']:
            case 'waterleakdetector':
                leak_sensor_info = BinarySensorInfo(name="LeakAlarm", device_class="moisture", unique_id="water_leak_sensor", device=device_info)
                leak_settings = Settings(mqtt=mqtt_settings, entity=leak_sensor_info)
                leak_sensor = BinarySensor(leak_settings)
                if device['water_alarm']:
                    leak_sensor.on()
                else:
                    leak_sensor.off()

                freeze_sensor_info = BinarySensorInfo(name="Low Temperature Alarm", device_class="cold", unique_id="freeze_sensor", device=device_info)
                freeze_settings = Settings(mqtt=mqtt_settings, entity=freeze_sensor_info)
                freeze_sensor = BinarySensor(freeze_settings)
                if device['low_temp_alarm']:
                    freeze_sensor.on()
                else:
                    freeze_sensor.off()

                temp_sensor_info =  NumberInfo(name="Temperature", mode="box", step=1, device_class="temperature", unique_id="temperature_sensor", device=device_info)
                temp_settings = Settings(mqtt=mqtt_settings, entity=temp_sensor_info)
                temp_sensor = Number(temp_settings, _noop_command_callback)
                temp_sensor.set_value(device['temperature'])

                last_seen_info =  TextInfo(name="Last Seen", unique_id="sensor_last_seen", device=device_info)
                last_seen_settings = Settings(mqtt=mqtt_settings, entity=last_seen_info)
                last_seen_sensor = Text(last_seen_settings, _noop_command_callback)
                last_seen_sensor.set_text(device['last_seen'])


                logger.debug(
                    f"MQTT Message sent for device {device['serial_number']}"
                )
                