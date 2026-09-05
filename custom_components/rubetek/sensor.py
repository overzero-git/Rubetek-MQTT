"""Sensor platform for Rubetek MQTT."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfFrequency,
    UnitOfPower,
    UnitOfReactivePower,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_DEVICE_TYPE,
    CONF_DEVICES,
    CONF_NAME,
    CONF_SERIAL,
    DOMAIN,
    TYPE_ENERGY_METER,
    TYPE_HEAT_METER,
    CONF_GATEWAY_UUID,
)
from .coordinator import DeviceKey, RubetekMqttCoordinator, RubetekState


@dataclass(frozen=True, kw_only=True)
class RubetekSensorDescription:
    key: str
    name: str
    value_fn: Callable[[RubetekState], Any]
    device_class: SensorDeviceClass | None = None
    state_class: SensorStateClass | None = None
    native_unit: str | None = None
    entity_category: EntityCategory | None = None
    icon: str | None = None


def _field(name: str) -> Callable[[RubetekState], Any]:
    return lambda state: state.data.get(name)


def _array(name: str, index: int) -> Callable[[RubetekState], Any]:
    def value(state: RubetekState) -> Any:
        data = state.data.get(name)
        if isinstance(data, list) and len(data) > index:
            return data[index]
        return None
    return value


def _energy(tariff: int, register: str) -> Callable[[RubetekState], Any]:
    def value(state: RubetekState) -> Any:
        data = state.data.get("energy")
        idx = tariff - 1
        if isinstance(data, list) and len(data) > idx and isinstance(data[idx], dict):
            return data[idx].get(register)
        return None
    return value


HEAT_SENSORS = [
    RubetekSensorDescription(key="temp_in", name="Температура подачи", value_fn=_field("temp_in"), device_class=SensorDeviceClass.TEMPERATURE, state_class=SensorStateClass.MEASUREMENT, native_unit=UnitOfTemperature.CELSIUS),
    RubetekSensorDescription(key="temp_out", name="Температура обратки", value_fn=_field("temp_out"), device_class=SensorDeviceClass.TEMPERATURE, state_class=SensorStateClass.MEASUREMENT, native_unit=UnitOfTemperature.CELSIUS),
    RubetekSensorDescription(key="flow", name="Расход", value_fn=_field("flow"), device_class=SensorDeviceClass.VOLUME_FLOW_RATE, state_class=SensorStateClass.MEASUREMENT, native_unit="m³/h"),
    RubetekSensorDescription(key="volume", name="Объём", value_fn=_field("volume"), device_class=SensorDeviceClass.VOLUME, state_class=SensorStateClass.TOTAL_INCREASING, native_unit="m³"),
    RubetekSensorDescription(key="e_sum_heat", name="Накопленное тепло", value_fn=_field("e_sum_heat"), device_class=SensorDeviceClass.ENERGY, state_class=SensorStateClass.TOTAL_INCREASING, native_unit="Gcal"),
    RubetekSensorDescription(key="e_sum_cold", name="Накопленный холод", value_fn=_field("e_sum_cold"), device_class=SensorDeviceClass.ENERGY, state_class=SensorStateClass.TOTAL_INCREASING, native_unit="Gcal"),
    RubetekSensorDescription(key="e_current", name="Текущая энергия", value_fn=_field("e_current"), device_class=SensorDeviceClass.ENERGY, state_class=SensorStateClass.MEASUREMENT, native_unit="kWh"),
    RubetekSensorDescription(key="work_time", name="Время работы", value_fn=_field("work_time"), device_class=SensorDeviceClass.DURATION, state_class=SensorStateClass.TOTAL_INCREASING, native_unit="h", entity_category=EntityCategory.DIAGNOSTIC),
    RubetekSensorDescription(key="rssi", name="RSSI", value_fn=_field("rssi"), device_class=SensorDeviceClass.SIGNAL_STRENGTH, state_class=SensorStateClass.MEASUREMENT, native_unit="dBm", entity_category=EntityCategory.DIAGNOSTIC),
    RubetekSensorDescription(key="min_rssi", name="Минимальный RSSI", value_fn=_field("min_rssi"), device_class=SensorDeviceClass.SIGNAL_STRENGTH, state_class=SensorStateClass.MEASUREMENT, native_unit="dBm", entity_category=EntityCategory.DIAGNOSTIC),
    RubetekSensorDescription(key="v_bat", name="Напряжение батареи", value_fn=_field("v_bat"), device_class=SensorDeviceClass.VOLTAGE, state_class=SensorStateClass.MEASUREMENT, native_unit=UnitOfElectricPotential.VOLT, entity_category=EntityCategory.DIAGNOSTIC),
    RubetekSensorDescription(key="error_code", name="Код ошибки", value_fn=_field("error_code"), entity_category=EntityCategory.DIAGNOSTIC),
]

ENERGY_SENSORS: list[RubetekSensorDescription] = [
    RubetekSensorDescription(key="frequency", name="Частота", value_fn=_field("frequency"), device_class=SensorDeviceClass.FREQUENCY, state_class=SensorStateClass.MEASUREMENT, native_unit=UnitOfFrequency.HERTZ),
    RubetekSensorDescription(key="kn", name="Коэффициент Kn", value_fn=_field("Kn"), entity_category=EntityCategory.DIAGNOSTIC),
    RubetekSensorDescription(key="kt", name="Коэффициент Kt", value_fn=_field("Kt"), entity_category=EntityCategory.DIAGNOSTIC),
    RubetekSensorDescription(key="error_code", name="Код ошибки", value_fn=_field("error_code"), entity_category=EntityCategory.DIAGNOSTIC),
]

for phase in range(3):
    n = phase + 1
    ENERGY_SENSORS.extend(
        [
            RubetekSensorDescription(key=f"voltage_l{n}", name=f"Напряжение L{n}", value_fn=_array("voltage", phase), device_class=SensorDeviceClass.VOLTAGE, state_class=SensorStateClass.MEASUREMENT, native_unit=UnitOfElectricPotential.VOLT),
            RubetekSensorDescription(key=f"current_l{n}", name=f"Ток L{n}", value_fn=_array("current", phase), device_class=SensorDeviceClass.CURRENT, state_class=SensorStateClass.MEASUREMENT, native_unit=UnitOfElectricCurrent.AMPERE),
            RubetekSensorDescription(key=f"active_l{n}", name=f"Активная мощность L{n}", value_fn=_array("active", phase), device_class=SensorDeviceClass.POWER, state_class=SensorStateClass.MEASUREMENT, native_unit=UnitOfPower.WATT),
            RubetekSensorDescription(key=f"reactive_l{n}", name=f"Реактивная мощность L{n}", value_fn=_array("reactive", phase), state_class=SensorStateClass.MEASUREMENT, native_unit=UnitOfReactivePower.VOLT_AMPERE_REACTIVE),
        ]
    )

for tariff in range(1, 5):
    for register in ("A+", "A-", "R+", "R-"):
        safe = register.replace("+", "plus").replace("-", "minus")
        ENERGY_SENSORS.append(
            RubetekSensorDescription(
                key=f"energy_t{tariff}_{safe}",
                name=f"Энергия T{tariff} {register}",
                value_fn=_energy(tariff, register),
            )
        )

LAST_SEEN = RubetekSensorDescription(
    key="last_seen",
    name="Последнее обновление",
    value_fn=lambda state: state.last_seen,
    device_class=SensorDeviceClass.TIMESTAMP,
    entity_category=EntityCategory.DIAGNOSTIC,
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: RubetekMqttCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[RubetekSensor] = []

    for configured in entry.options.get(CONF_DEVICES, []):
        device_type = str(configured[CONF_DEVICE_TYPE])
        serial = int(configured[CONF_SERIAL])
        friendly_name = str(configured.get(CONF_NAME) or serial)

        if device_type == TYPE_HEAT_METER:
            descriptions = [*HEAT_SENSORS, LAST_SEEN]
        elif device_type == TYPE_ENERGY_METER:
            descriptions = [*ENERGY_SENSORS, LAST_SEEN]
        else:
            continue

        entities.extend(
            RubetekSensor(coordinator, device_type, serial, friendly_name, description)
            for description in descriptions
        )

    async_add_entities(entities)


class RubetekSensor(SensorEntity):
    """One normalized Rubetek meter sensor."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: RubetekMqttCoordinator,
        device_type: str,
        serial: int,
        friendly_name: str,
        description: RubetekSensorDescription,
    ) -> None:
        self.coordinator = coordinator
        self.key: DeviceKey = (device_type, serial)
        self.device_type = device_type
        self.serial = serial
        self.friendly_name = friendly_name
        self.description = description
        self._attr_name = description.name
        self._attr_unique_id = f"{device_type}_{serial}_{description.key}"
        self._attr_device_class = description.device_class
        self._attr_state_class = description.state_class
        self._attr_native_unit_of_measurement = description.native_unit
        self._attr_entity_category = description.entity_category
        self._attr_icon = description.icon
        self._remove_listener: Callable[[], None] | None = None

    @property
    def available(self) -> bool:
        return self.coordinator.get_state(self.key) is not None

    @property
    def native_value(self) -> Any:
        state = self.coordinator.get_state(self.key)
        if state is None:
            return None
        return self.description.value_fn(state)

    @property
    def device_info(self) -> DeviceInfo:
        state = self.coordinator.get_state(self.key)
        model = None
        sw_version = None
        if state:
            model = state.data.get("model")
            sw_version = state.data.get("modem_fw_ver")

        return DeviceInfo(
            identifiers={(DOMAIN, f"{self.device_type}:{self.serial}")},
            via_device=(DOMAIN, f"gateway:{self.coordinator.gateway_uuid}"),
            name=self.friendly_name,
            manufacturer="Rubetek",
            model=str(model) if model else ("Теплосчётчик" if self.device_type == TYPE_HEAT_METER else "Электросчётчик"),
            sw_version=str(sw_version) if sw_version else None,
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        state = self.coordinator.get_state(self.key)
        if state is None:
            return {}
        data = state.data
        return {
            "gateway_uuid": state.gateway_uuid,
            "gateway_sn": state.gateway_sn,
            "trx_sn": data.get("trx_sn"),
            "min_trx_sn": data.get("min_trx_sn"),
            "meter_model": data.get("model"),
            "modem_fw_ver": data.get("modem_fw_ver"),
        }

    async def async_added_to_hass(self) -> None:
        @callback
        def _update() -> None:
            self.async_write_ha_state()

        self._remove_listener = self.coordinator.async_add_listener(self.key, _update)

    async def async_will_remove_from_hass(self) -> None:
        if self._remove_listener:
            self._remove_listener()
            self._remove_listener = None
