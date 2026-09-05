"""Config flow for Rubetek MQTT."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_DEVICE_TYPE,
    CONF_DEVICES,
    CONF_GATEWAY_FW,
    CONF_GATEWAY_MODEL,
    CONF_GATEWAY_SN,
    CONF_GATEWAY_UUID,
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SERIAL,
    CONF_USERNAME,
    DEFAULT_PORT,
    DISCOVERY_TIMEOUT,
    DOMAIN,
    SUPPORTED_DEVICE_TYPES,
)
from .mqtt_transport import MqttError, SimpleMqttClient


def _device_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(CONF_DEVICE_TYPE, default=defaults.get(CONF_DEVICE_TYPE, "heat_meter")): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[selector.SelectOptionDict(value=value, label=label) for value, label in SUPPORTED_DEVICE_TYPES.items()],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Required(CONF_SERIAL, default=defaults.get(CONF_SERIAL, "")): selector.TextSelector(),
            vol.Required(CONF_NAME, default=defaults.get(CONF_NAME, "")): selector.TextSelector(),
        }
    )


class RubetekConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Configure a Rubetek USPD connection."""

    VERSION = 2

    def __init__(self) -> None:
        self._broker: dict[str, Any] = {}
        self._gateways: dict[str, dict[str, Any]] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._broker = {
                CONF_HOST: str(user_input[CONF_HOST]).strip(),
                CONF_PORT: int(user_input[CONF_PORT]),
                CONF_USERNAME: str(user_input.get(CONF_USERNAME) or "").strip(),
                CONF_PASSWORD: str(user_input.get(CONF_PASSWORD) or ""),
            }
            try:
                self._gateways = await self._discover_gateways()
            except MqttError as err:
                text = str(err).lower()
                errors["base"] = "invalid_auth" if "password" in text or "authorized" in text else "cannot_connect"
            else:
                if self._gateways:
                    return await self.async_step_gateway()
                return await self.async_step_manual_gateway()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST, default=(user_input or {}).get(CONF_HOST, "")): str,
                    vol.Required(CONF_PORT, default=(user_input or {}).get(CONF_PORT, DEFAULT_PORT)): vol.Coerce(int),
                    vol.Optional(CONF_USERNAME, default=(user_input or {}).get(CONF_USERNAME, "")): str,
                    vol.Optional(CONF_PASSWORD, default=(user_input or {}).get(CONF_PASSWORD, "")): selector.TextSelector(
                        selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                    ),
                }
            ),
            errors=errors,
        )

    async def _discover_gateways(self) -> dict[str, dict[str, Any]]:
        found: dict[str, dict[str, Any]] = {}
        client = SimpleMqttClient(
            self._broker[CONF_HOST],
            self._broker[CONF_PORT],
            self._broker.get(CONF_USERNAME),
            self._broker.get(CONF_PASSWORD),
            client_id="ha-rubetek-discovery",
        )

        async def on_message(topic: str, raw: bytes) -> None:
            parts = topic.split("/")
            topic_uuid = parts[1] if len(parts) >= 3 and parts[0] == "metering" else None
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                payload = {}
            params = payload.get("params") if isinstance(payload, dict) else None
            params = params if isinstance(params, dict) else {}
            uuid = str(params.get("uuid") or topic_uuid or "").strip()
            if not uuid:
                return
            item = found.setdefault(uuid, {CONF_GATEWAY_UUID: uuid})
            if params.get("sn") is not None:
                item[CONF_GATEWAY_SN] = params.get("sn")
            if params.get("model"):
                item[CONF_GATEWAY_MODEL] = params.get("model")
            if params.get("fw_ver"):
                item[CONF_GATEWAY_FW] = params.get("fw_ver")

        try:
            await client.connect()
            await client.subscribe("metering/#", on_message)
            await asyncio.sleep(DISCOVERY_TIMEOUT)
        finally:
            await client.disconnect()
        return found

    async def async_step_gateway(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            uuid = str(user_input[CONF_GATEWAY_UUID])
            gateway = self._gateways[uuid]
            await self.async_set_unique_id(uuid)
            self._abort_if_unique_id_configured()
            data = {**self._broker, **gateway}
            model = gateway.get(CONF_GATEWAY_MODEL) or "УСПД"
            sn = gateway.get(CONF_GATEWAY_SN)
            title = f"Rubetek {model} {sn}" if sn else f"Rubetek {uuid}"
            return self.async_create_entry(title=title, data=data, options={CONF_DEVICES: []})

        options = []
        for uuid, gateway in self._gateways.items():
            model = gateway.get(CONF_GATEWAY_MODEL) or "УСПД"
            sn = gateway.get(CONF_GATEWAY_SN)
            label = f"{model} — SN {sn} — {uuid}" if sn else f"{model} — {uuid}"
            options.append(selector.SelectOptionDict(value=uuid, label=label))
        return self.async_show_form(
            step_id="gateway",
            data_schema=vol.Schema({
                vol.Required(CONF_GATEWAY_UUID): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=options, mode=selector.SelectSelectorMode.DROPDOWN)
                )
            }),
        )


    async def async_step_manual_gateway(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Allow manual UUID when the broker has no retained/live metering packets."""
        errors: dict[str, str] = {}
        if user_input is not None:
            uuid = str(user_input[CONF_GATEWAY_UUID]).strip()
            if not uuid:
                errors[CONF_GATEWAY_UUID] = "invalid_uuid"
            else:
                await self.async_set_unique_id(uuid)
                self._abort_if_unique_id_configured()
                data = {**self._broker, CONF_GATEWAY_UUID: uuid}
                return self.async_create_entry(
                    title=f"Rubetek {uuid}",
                    data=data,
                    options={CONF_DEVICES: []},
                )

        return self.async_show_form(
            step_id="manual_gateway",
            data_schema=vol.Schema({vol.Required(CONF_GATEWAY_UUID): str}),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> RubetekOptionsFlow:
        return RubetekOptionsFlow()


class RubetekOptionsFlow(config_entries.OptionsFlow):
    """Manage manually approved end devices for one USPD."""

    def _devices(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self.config_entry.options.get(CONF_DEVICES, [])]

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        menu = ["add_device"]
        if self._devices():
            menu.append("remove_device")
        return self.async_show_menu(step_id="init", menu_options=menu)

    async def async_step_add_device(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                serial = int(str(user_input[CONF_SERIAL]).strip())
                if serial <= 0:
                    raise ValueError
            except (TypeError, ValueError):
                errors[CONF_SERIAL] = "invalid_serial"
            else:
                device_type = str(user_input[CONF_DEVICE_TYPE])
                # Prevent the same physical end meter from being configured under two USPD entries.
                for entry in self.hass.config_entries.async_entries(DOMAIN):
                    for item in entry.options.get(CONF_DEVICES, []):
                        if item.get(CONF_DEVICE_TYPE) == device_type and int(item.get(CONF_SERIAL, -1)) == serial:
                            errors[CONF_SERIAL] = "already_configured"
                            break
                    if errors:
                        break
                if not errors:
                    devices = self._devices()
                    devices.append({
                        CONF_DEVICE_TYPE: device_type,
                        CONF_SERIAL: serial,
                        CONF_NAME: str(user_input[CONF_NAME]).strip() or str(serial),
                    })
                    return self.async_create_entry(title="", data={**self.config_entry.options, CONF_DEVICES: devices})

        return self.async_show_form(step_id="add_device", data_schema=_device_schema(user_input), errors=errors)

    async def async_step_remove_device(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        devices = self._devices()
        choices = {
            f"{item[CONF_DEVICE_TYPE]}:{item[CONF_SERIAL]}": f"{item.get(CONF_NAME, item[CONF_SERIAL])} ({item[CONF_SERIAL]})"
            for item in devices
        }
        if user_input is not None:
            device_type, serial_text = user_input["device"].split(":", 1)
            serial = int(serial_text)
            devices = [
                item for item in devices
                if not (item.get(CONF_DEVICE_TYPE) == device_type and int(item.get(CONF_SERIAL, -1)) == serial)
            ]
            return self.async_create_entry(title="", data={**self.config_entry.options, CONF_DEVICES: devices})
        return self.async_show_form(
            step_id="remove_device",
            data_schema=vol.Schema({
                vol.Required("device"): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[selector.SelectOptionDict(value=value, label=label) for value, label in choices.items()]
                    )
                )
            }),
        )
