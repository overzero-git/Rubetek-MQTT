"""Rubetek MQTT integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .const import (
    CONF_GATEWAY_FW,
    CONF_GATEWAY_MODEL,
    CONF_GATEWAY_SN,
    CONF_GATEWAY_UUID,
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_USERNAME,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import RubetekMqttCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up one Rubetek USPD config entry."""
    uuid = str(entry.data[CONF_GATEWAY_UUID])
    coordinator = RubetekMqttCoordinator(
        hass,
        host=str(entry.data[CONF_HOST]),
        port=int(entry.data[CONF_PORT]),
        username=str(entry.data.get(CONF_USERNAME) or "") or None,
        password=str(entry.data.get(CONF_PASSWORD) or "") or None,
        gateway_uuid=uuid,
    )
    await coordinator.async_start()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    registry = dr.async_get(hass)
    registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"gateway:{uuid}")},
        manufacturer="Rubetek",
        name=f"Rubetek {entry.data.get(CONF_GATEWAY_MODEL) or 'УСПД'}",
        model=str(entry.data.get(CONF_GATEWAY_MODEL) or "УСПД"),
        serial_number=str(entry.data.get(CONF_GATEWAY_SN)) if entry.data.get(CONF_GATEWAY_SN) is not None else None,
        sw_version=str(entry.data.get(CONF_GATEWAY_FW)) if entry.data.get(CONF_GATEWAY_FW) else None,
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))
    return True


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator: RubetekMqttCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_stop()
    return unload_ok
