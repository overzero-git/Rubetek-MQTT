"""Constants for Rubetek MQTT."""

DOMAIN = "rubetek"
PLATFORMS = ["sensor"]

CONF_HOST = "host"
CONF_PORT = "port"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_GATEWAY_UUID = "gateway_uuid"
CONF_GATEWAY_SN = "gateway_sn"
CONF_GATEWAY_MODEL = "gateway_model"
CONF_GATEWAY_FW = "gateway_fw"

CONF_DEVICES = "devices"
CONF_DEVICE_TYPE = "device_type"
CONF_SERIAL = "serial"
CONF_NAME = "name"

TYPE_HEAT_METER = "heat_meter"
TYPE_ENERGY_METER = "energy_meter"
SUPPORTED_DEVICE_TYPES = {
    TYPE_HEAT_METER: "Теплосчётчик",
    TYPE_ENERGY_METER: "Электросчётчик",
}

DEFAULT_PORT = 1883
DISCOVERY_TIMEOUT = 5.0
