"""Small asyncio MQTT 3.1.1 client used by Rubetek MQTT.

It intentionally implements only what the Rubetek integration needs:
CONNECT with optional username/password, SUBSCRIBE, PING and inbound PUBLISH.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import logging

_LOGGER = logging.getLogger(__name__)

MessageCallback = Callable[[str, bytes], Awaitable[None] | None]


class MqttError(Exception):
    """MQTT transport error."""


def _enc_varint(value: int) -> bytes:
    out = bytearray()
    while True:
        digit = value % 128
        value //= 128
        if value:
            digit |= 0x80
        out.append(digit)
        if not value:
            return bytes(out)


def _enc_utf8(value: str) -> bytes:
    raw = value.encode("utf-8")
    return len(raw).to_bytes(2, "big") + raw


async def _read_varint(reader: asyncio.StreamReader) -> int:
    multiplier = 1
    value = 0
    for _ in range(4):
        digit = (await reader.readexactly(1))[0]
        value += (digit & 127) * multiplier
        if not digit & 128:
            return value
        multiplier *= 128
    raise MqttError("Malformed MQTT remaining length")


class SimpleMqttClient:
    """Minimal MQTT 3.1.1 client."""

    def __init__(
        self,
        host: str,
        port: int = 1883,
        username: str | None = None,
        password: str | None = None,
        *,
        client_id: str = "homeassistant-rubetek",
    ) -> None:
        self.host = host
        self.port = port
        self.username = username or None
        self.password = password or None
        self.client_id = client_id
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self._packet_id = 1
        self._read_task: asyncio.Task[None] | None = None
        self._ping_task: asyncio.Task[None] | None = None
        self._callback: MessageCallback | None = None
        self.connected = False

    async def connect(self, timeout: float = 10.0) -> None:
        """Open TCP connection and perform MQTT CONNECT."""
        try:
            self.reader, self.writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), timeout=timeout
            )
        except (OSError, TimeoutError, asyncio.TimeoutError) as err:
            raise MqttError(f"Cannot connect to {self.host}:{self.port}: {err}") from err

        flags = 0x02  # clean session
        payload = _enc_utf8(self.client_id)
        if self.username is not None:
            flags |= 0x80
            payload += _enc_utf8(self.username)
        if self.password is not None:
            flags |= 0x40
            payload += _enc_utf8(self.password)

        variable = _enc_utf8("MQTT") + bytes([4, flags]) + (60).to_bytes(2, "big")
        await self._send(bytes([0x10]) + _enc_varint(len(variable) + len(payload)) + variable + payload)

        try:
            header = await asyncio.wait_for(self.reader.readexactly(1), timeout=timeout)
            remaining = await asyncio.wait_for(_read_varint(self.reader), timeout=timeout)
            body = await asyncio.wait_for(self.reader.readexactly(remaining), timeout=timeout)
        except (asyncio.IncompleteReadError, TimeoutError, asyncio.TimeoutError) as err:
            await self.disconnect()
            raise MqttError("No valid CONNACK from broker") from err

        if header[0] >> 4 != 2 or len(body) < 2:
            await self.disconnect()
            raise MqttError("Invalid CONNACK")
        rc = body[1]
        if rc != 0:
            await self.disconnect()
            messages = {
                1: "unacceptable protocol version",
                2: "identifier rejected",
                3: "server unavailable",
                4: "bad username or password",
                5: "not authorized",
            }
            raise MqttError(f"Broker rejected connection: {messages.get(rc, rc)}")
        self.connected = True

    async def subscribe(self, topic: str, callback: MessageCallback, qos: int = 0) -> None:
        """Subscribe and start receiver tasks."""
        if not self.writer or not self.reader:
            raise MqttError("Not connected")
        self._callback = callback
        packet_id = self._next_packet_id()
        body = packet_id.to_bytes(2, "big") + _enc_utf8(topic) + bytes([qos])
        await self._send(bytes([0x82]) + _enc_varint(len(body)) + body)
        if self._read_task is None:
            self._read_task = asyncio.create_task(self._reader_loop())
            self._ping_task = asyncio.create_task(self._ping_loop())

    async def disconnect(self) -> None:
        """Close MQTT connection."""
        self.connected = False
        current = asyncio.current_task()
        for task in (self._read_task, self._ping_task):
            if task and task is not current:
                task.cancel()
        self._read_task = None
        self._ping_task = None
        if self.writer:
            try:
                self.writer.write(b"\xe0\x00")
                await self.writer.drain()
            except (OSError, ConnectionError):
                pass
            self.writer.close()
            try:
                await self.writer.wait_closed()
            except (OSError, ConnectionError):
                pass
        self.reader = None
        self.writer = None

    async def _send(self, packet: bytes) -> None:
        if not self.writer:
            raise MqttError("Not connected")
        self.writer.write(packet)
        await self.writer.drain()

    def _next_packet_id(self) -> int:
        value = self._packet_id
        self._packet_id = 1 if value >= 65535 else value + 1
        return value

    async def _ping_loop(self) -> None:
        try:
            while self.connected:
                await asyncio.sleep(30)
                await self._send(b"\xc0\x00")
        except (asyncio.CancelledError, OSError, ConnectionError, MqttError):
            return

    async def _reader_loop(self) -> None:
        assert self.reader is not None
        try:
            while self.connected:
                first = (await self.reader.readexactly(1))[0]
                remaining = await _read_varint(self.reader)
                body = await self.reader.readexactly(remaining)
                packet_type = first >> 4
                if packet_type == 3:
                    await self._handle_publish(first, body)
        except asyncio.CancelledError:
            return
        except (asyncio.IncompleteReadError, OSError, ConnectionError, MqttError) as err:
            _LOGGER.debug("Rubetek MQTT receiver stopped: %s", err)
        finally:
            self.connected = False

    async def _handle_publish(self, first: int, body: bytes) -> None:
        if len(body) < 2:
            return
        topic_len = int.from_bytes(body[:2], "big")
        if len(body) < 2 + topic_len:
            return
        topic = body[2 : 2 + topic_len].decode("utf-8", errors="replace")
        pos = 2 + topic_len
        qos = (first >> 1) & 0x03
        packet_id = None
        if qos:
            if len(body) < pos + 2:
                return
            packet_id = int.from_bytes(body[pos : pos + 2], "big")
            pos += 2
        payload = body[pos:]

        if self._callback:
            result = self._callback(topic, payload)
            if asyncio.iscoroutine(result):
                await result

        if qos == 1 and packet_id is not None:
            await self._send(b"\x40\x02" + packet_id.to_bytes(2, "big"))
