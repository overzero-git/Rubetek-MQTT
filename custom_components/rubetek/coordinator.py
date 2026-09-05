"""MQTT data handling for Rubetek MQTT."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from homeassistant.core import HomeAssistant, callback
from homeassistant.util import dt as dt_util

from .mqtt_transport import MqttError, SimpleMqttClient

_LOGGER = logging.getLogger(__name__)

DeviceKey = tuple[str, int]


@dataclass(slots=True)
class RubetekState:
    """Latest state for one physical end device."""

    data: dict[str, Any] = field(default_factory=dict)
    gateway_uuid: str | None = None
    gateway_sn: int | str | None = None
    last_seen: datetime | None = None


class RubetekMqttCoordinator:
    """Listen to one Rubetek USPD and fan updates out to entities."""

    def __init__(
        self,
        hass: HomeAssistant,
        host: str,
        port: int,
        username: str | None,
        password: str | None,
        gateway_uuid: str,
    ) -> None:
        self.hass = hass
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.gateway_uuid = gateway_uuid
        self.states: dict[DeviceKey, RubetekState] = {}
        self.gateway: dict[str, Any] = {}
        self._listeners: dict[DeviceKey, set[Callable[[], None]]] = {}
        self._client: SimpleMqttClient | None = None
        self._runner: asyncio.Task[None] | None = None
        self._stopping = False

    async def async_start(self) -> None:
        """Start reconnecting MQTT worker."""
        self._stopping = False
        self._runner = asyncio.create_task(self._run())

    async def async_stop(self) -> None:
        """Stop MQTT worker."""
        self._stopping = True
        if self._runner:
            self._runner.cancel()
            try:
                await self._runner
            except asyncio.CancelledError:
                pass
            self._runner = None
        if self._client:
            await self._client.disconnect()
            self._client = None

    async def _run(self) -> None:
        delay = 2
        while not self._stopping:
            client = SimpleMqttClient(
                self.host,
                self.port,
                self.username,
                self.password,
                client_id=f"ha-rubetek-{self.gateway_uuid[-8:]}",
            )
            self._client = client
            try:
                await client.connect()
                await client.subscribe(f"metering/{self.gateway_uuid}/#", self._on_message)
                _LOGGER.info(
                    "Rubetek MQTT connected to %s:%s, gateway %s",
                    self.host,
                    self.port,
                    self.gateway_uuid,
                )
                delay = 2
                while client.connected and not self._stopping:
                    await asyncio.sleep(1)
            except (MqttError, OSError) as err:
                _LOGGER.warning("Rubetek MQTT connection error: %s", err)
            finally:
                await client.disconnect()
            if not self._stopping:
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60)

    async def _on_message(self, topic: str, raw: bytes) -> None:
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
            return

        if payload.get("method") == "device_status":
            params = payload.get("params") or {}
            if str(params.get("uuid")) == self.gateway_uuid:
                self.gateway = dict(params)
            return

        if payload.get("method") != "metering":
            return

        params = payload.get("params") or {}
        if str(params.get("uuid")) != self.gateway_uuid:
            return
        device = params.get("device") or {}
        device_type = device.get("type")
        meter_sn = device.get("meter_sn")
        if not device_type or meter_sn is None:
            return
        try:
            serial = int(meter_sn)
        except (TypeError, ValueError):
            return

        key: DeviceKey = (str(device_type), serial)
        state = self.states.setdefault(key, RubetekState())
        state.data = dict(device)
        state.gateway_uuid = self.gateway_uuid
        state.gateway_sn = params.get("sn")
        state.last_seen = dt_util.utcnow()

        for listener in tuple(self._listeners.get(key, ())):
            listener()

    @callback
    def async_add_listener(self, key: DeviceKey, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.setdefault(key, set()).add(listener)

        @callback
        def _remove() -> None:
            listeners = self._listeners.get(key)
            if listeners is None:
                return
            listeners.discard(listener)
            if not listeners:
                self._listeners.pop(key, None)

        return _remove

    def get_state(self, key: DeviceKey) -> RubetekState | None:
        return self.states.get(key)
