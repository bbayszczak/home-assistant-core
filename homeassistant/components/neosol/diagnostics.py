"""Diagnostics support for Profalux Neosol."""

from typing import Any

from homeassistant.core import HomeAssistant

from .coordinator import NeosolConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: NeosolConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    info = coordinator.info

    # The channel keys command the shutters and the serial numbers identify the user's
    # own hardware: neither belongs in a diagnostics file meant to be shared.
    return {
        "dongle": {
            "hardware_version": info.hardware_version,
            "software_version": info.software_version,
            "frame_repeat": info.frame_repeat,
            "return_code_active": info.return_code_active,
            "read_protection": info.read_protection,
        },
        "channels": [
            {"index": channel.index, "sync": channel.sync}
            for channel in coordinator.data.values()
        ],
    }
