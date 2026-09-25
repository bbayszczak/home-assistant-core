"""Cover platform for the Profalux Neosol integration."""

from collections.abc import Callable
from functools import partial
from typing import Any, override

from pyneosol import Action, NeosolError

from homeassistant.components.cover import (
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .coordinator import NeosolConfigEntry, NeosolCoordinator
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
                NeosolCover(coordinator, channel, known_channels.discard)
                for channel in sorted(new_channels)
            )

    _async_add_new_shutters()
    entry.async_on_unload(coordinator.async_add_listener(_async_add_new_shutters))


class NeosolCover(NeosolEntity, CoverEntity):
    """A roller shutter driven by one channel of the dongle."""

    # The radio link is one way: a sent frame is never acknowledged and no position can
    # be read back, so whether a shutter is open is simply unknown, and stays that way.
    # Reporting what the last command implied would be a guess the shutter never
    # confirmed, and a shutter driven from its own remote would make it wrong.
    _attr_assumed_state = True
    _attr_device_class = CoverDeviceClass.SHUTTER
    _attr_is_closed: bool | None = None
    _attr_supported_features = (
        CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE | CoverEntityFeature.STOP
    )

    def __init__(
        self,
        coordinator: NeosolCoordinator,
        channel: int,
        release_channel: Callable[[int], None],
    ) -> None:
        """Initialize the shutter, and what to call once it leaves Home Assistant."""
        super().__init__(coordinator, channel)
        self._release_channel = release_channel

    @override
    async def async_added_to_hass(self) -> None:
        """Let the platform add this channel again once the shutter is removed."""
        await super().async_added_to_hass()
        # An unpaired or forgotten shutter frees its channel, which a later pairing can
        # reuse: the platform must then see it as new, not as already added.
        self.async_on_remove(partial(self._release_channel, self.channel))

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

    @override
    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close the shutter."""
        await self._async_send(Action.CLOSE)

    @override
    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Stop the shutter mid-travel."""
        await self._async_send(Action.STOP)
