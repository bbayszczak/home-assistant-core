"""Cover platform for the Profalux Neosol integration."""

from typing import Any, override

from pyneosol import Action, NeosolError

from homeassistant.components.cover import (
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
    CoverState,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN
from .coordinator import NeosolConfigEntry
from .entity import NeosolEntity

# One serial link, one AT command at a time.
PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: NeosolConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the shutters exposed by the dongle."""
    coordinator = entry.runtime_data
    known_channels: set[int] = set()

    @callback
    def _async_add_new_shutters() -> None:
        """Add the channels that have been paired since the last refresh."""
        if new_channels := coordinator.data.keys() - known_channels:
            known_channels.update(new_channels)
            async_add_entities(
                NeosolCover(coordinator, channel) for channel in sorted(new_channels)
            )

    _async_add_new_shutters()
    entry.async_on_unload(coordinator.async_add_listener(_async_add_new_shutters))


class NeosolCover(NeosolEntity, RestoreEntity, CoverEntity):
    """A roller shutter driven by one channel of the dongle."""

    # The radio link is one way: a sent frame is never acknowledged and no position can
    # be read back, so every state this entity reports is what the last command implied.
    _attr_assumed_state = True
    _attr_device_class = CoverDeviceClass.SHUTTER
    # Nothing is known until a command has been sent, or an old state restored.
    _attr_is_closed: bool | None = None
    _attr_supported_features = (
        CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE | CoverEntityFeature.STOP
    )

    @override
    async def async_added_to_hass(self) -> None:
        """Restore the state assumed before the restart."""
        await super().async_added_to_hass()

        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state in (
            CoverState.OPEN,
            CoverState.CLOSED,
        ):
            self._attr_is_closed = last_state.state == CoverState.CLOSED

    async def _async_send(self, action: Action) -> None:
        """Transmit ``action`` on this channel."""
        try:
            await self.coordinator.dongle.send(self.channel, action)
        except NeosolError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="send_failed",
                translation_placeholders={
                    "channel": str(self.channel),
                    "error": str(err),
                },
            ) from err

    @override
    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open the shutter."""
        await self._async_send(Action.OPEN)
        self._attr_is_closed = False
        self.async_write_ha_state()

    @override
    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close the shutter."""
        await self._async_send(Action.CLOSE)
        self._attr_is_closed = True
        self.async_write_ha_state()

    @override
    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Stop the shutter mid-travel.

        The position it stops at is unknown, so the assumed state is left untouched.
        """
        await self._async_send(Action.STOP)
