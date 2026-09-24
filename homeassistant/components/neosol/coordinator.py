"""Coordinator for the Profalux Neosol integration."""

from datetime import timedelta
from typing import override

from pyneosol import Channel, Dongle, DongleInfo, NeosolError, TransportError

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import CONF_IGNORED_CHANNELS, DOMAIN, LOGGER

type NeosolConfigEntry = ConfigEntry[NeosolCoordinator]

# The channel table only changes when the user pairs a shutter, which is a manual
# operation on the dongle itself. The poll is therefore a liveness check first, and the
# way newly paired shutters show up second.
SCAN_INTERVAL = timedelta(minutes=5)


async def open_dongle(port: str) -> tuple[Dongle, DongleInfo]:
    """Open the dongle on ``port`` and read its identification.

    Opening with ``verify=False`` and reading the info explicitly keeps it to a single
    ``AT&V`` exchange, while still raising ``NotADongleError`` on an incompatible device.
    """
    dongle = await Dongle.open(port, verify=False)
    try:
        return dongle, await dongle.info()
    except NeosolError:
        await dongle.close()
        raise


class NeosolCoordinator(DataUpdateCoordinator[dict[int, Channel]]):
    """Keep track of the channels the dongle exposes, keyed by channel index."""

    config_entry: NeosolConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: NeosolConfigEntry,
        dongle: Dongle,
        info: DongleInfo,
    ) -> None:
        """Initialize the coordinator around an already opened dongle."""
        super().__init__(
            hass,
            LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
        )
        self.dongle = dongle
        self.info = info

    @property
    def ignored_channels(self) -> set[int]:
        """Return the channels kept out of the shutters."""
        return set(self.config_entry.options.get(CONF_IGNORED_CHANNELS, []))

    @callback
    def async_ignore_channel(self, channel: int) -> None:
        """Keep ``channel`` out of the shutters from now on."""
        self._async_set_ignored_channels(self.ignored_channels | {channel})

    @callback
    def async_unignore_channel(self, channel: int) -> None:
        """Let ``channel`` show up as a shutter again."""
        self._async_set_ignored_channels(self.ignored_channels - {channel})

    @callback
    def _async_set_ignored_channels(self, channels: set[int]) -> None:
        """Store ``channels`` as the ones kept out of the shutters."""
        entry = self.config_entry
        self.hass.config_entries.async_update_entry(
            entry, options={**entry.options, CONF_IGNORED_CHANNELS: sorted(channels)}
        )

    @override
    async def _async_update_data(self) -> dict[int, Channel]:
        """Read the paired channels from the dongle."""
        try:
            channels = await self.dongle.used_channels()
        except TransportError as err:
            # The serial link is gone, typically an unplugged dongle. The transport
            # cannot recover on its own, only reopening the port does. Reloading is what
            # reopens it, but only once the entry is up: during setup the retry that
            # ConfigEntryNotReady triggers already does the same, and scheduling a reload
            # from there would restart the setup in a loop.
            if self.config_entry.state is ConfigEntryState.LOADED:
                self.hass.config_entries.async_schedule_reload(
                    self.config_entry.entry_id
                )
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="lost_connection",
                translation_placeholders={"error": str(err)},
            ) from err
        except NeosolError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="channel_table_failed",
                translation_placeholders={"error": str(err)},
            ) from err

        ignored = self.ignored_channels
        return {
            channel.index: channel
            for channel in channels
            if channel.index not in ignored
        }
