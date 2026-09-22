"""Tests for the Profalux Neosol setup and teardown."""

from unittest.mock import MagicMock

from freezegun.api import FrozenDateTimeFactory
from pyneosol import DongleNotFoundError, NotADongleError, ProtocolError, TransportError
import pytest
from syrupy.assertion import SnapshotAssertion

from homeassistant.components.neosol import async_remove_config_entry_device
from homeassistant.components.neosol.const import CONF_IGNORED_CHANNELS, DOMAIN
from homeassistant.components.neosol.coordinator import SCAN_INTERVAL
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from . import MOCK_SERIAL, setup_integration

from tests.common import MockConfigEntry, async_fire_time_changed


@pytest.mark.usefixtures("mock_dongle")
async def test_setup_and_unload(
    hass: HomeAssistant, mock_dongle: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """Test the entry loads and releases the serial port when unloaded."""
    await setup_integration(hass, mock_config_entry)
    assert mock_config_entry.state is ConfigEntryState.LOADED
    mock_dongle.close.assert_not_awaited()

    await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    assert mock_config_entry.state is ConfigEntryState.NOT_LOADED
    mock_dongle.close.assert_awaited_once()


@pytest.mark.parametrize(
    ("failing_call", "exception"),
    [
        pytest.param("open", DongleNotFoundError("no such port"), id="no-port"),
        pytest.param("info", NotADongleError("no marker"), id="not-a-dongle"),
        pytest.param(
            "used_channels", TransportError("link died"), id="channel-table-failed"
        ),
    ],
)
async def test_setup_failure_retries(
    hass: HomeAssistant,
    mock_dongle_class: MagicMock,
    mock_config_entry: MockConfigEntry,
    failing_call: str,
    exception: Exception,
) -> None:
    """Test a dongle that cannot be reached puts the entry in retry."""
    target = (
        mock_dongle_class.open
        if failing_call == "open"
        else getattr(mock_dongle_class.open.return_value, failing_call)
    )
    target.side_effect = exception

    await setup_integration(hass, mock_config_entry)

    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_port_released_when_first_refresh_fails(
    hass: HomeAssistant, mock_dongle: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """Test the port is not left open when the first channel read fails."""
    mock_dongle.used_channels.side_effect = TransportError("link died")

    await setup_integration(hass, mock_config_entry)

    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY
    mock_dongle.close.assert_awaited_once()


@pytest.mark.usefixtures("mock_dongle")
async def test_devices(
    hass: HomeAssistant,
    device_registry: dr.DeviceRegistry,
    mock_config_entry: MockConfigEntry,
    snapshot: SnapshotAssertion,
) -> None:
    """Test the dongle and its shutters are registered as devices."""
    await setup_integration(hass, mock_config_entry)

    devices = dr.async_entries_for_config_entry(
        device_registry, mock_config_entry.entry_id
    )
    assert sorted(devices, key=lambda device: sorted(device.identifiers)) == snapshot


async def test_lost_link_reloads_the_entry(
    hass: HomeAssistant,
    mock_dongle_class: MagicMock,
    mock_dongle: MagicMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test an unplugged dongle hands the entry back to the setup retry."""
    await setup_integration(hass, mock_config_entry)
    assert mock_dongle_class.open.call_count == 1

    mock_dongle.used_channels.side_effect = TransportError("link died")
    freezer.tick(SCAN_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    # Reopening the port is the only way back, so the entry is reloaded.
    assert mock_dongle_class.open.call_count == 2
    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_channel_table_error_keeps_the_entry(
    hass: HomeAssistant,
    mock_dongle_class: MagicMock,
    mock_dongle: MagicMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test a garbled answer only marks the shutters unavailable."""
    await setup_integration(hass, mock_config_entry)

    mock_dongle.used_channels.side_effect = ProtocolError("garbled answer")
    freezer.tick(SCAN_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    # The link is still up, so there is nothing a reload would fix.
    assert mock_dongle_class.open.call_count == 1
    assert hass.states.get("cover.shutter_0").state == STATE_UNAVAILABLE


@pytest.mark.usefixtures("mock_dongle")
async def test_the_dongle_cannot_be_removed(
    hass: HomeAssistant,
    device_registry: dr.DeviceRegistry,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test the dongle device is not removable, since the entry owns it."""
    await setup_integration(hass, mock_config_entry)
    device = device_registry.async_get_device_by_identifier(
        (DOMAIN, MOCK_SERIAL), mock_config_entry.entry_id
    )

    assert (
        await async_remove_config_entry_device(hass, mock_config_entry, device) is False
    )


@pytest.mark.usefixtures("mock_dongle")
async def test_a_removed_shutter_stays_away(
    hass: HomeAssistant,
    device_registry: dr.DeviceRegistry,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test a removed shutter is not brought back by the next refresh.

    A pairing attempt that did not take leaves its channel looking paired, so removing
    the shutter it created has to be remembered.
    """
    await setup_integration(hass, mock_config_entry)
    device = device_registry.async_get_device_by_identifier(
        (DOMAIN, f"{MOCK_SERIAL}_1"), mock_config_entry.entry_id
    )

    assert (
        await async_remove_config_entry_device(hass, mock_config_entry, device) is True
    )
    await hass.async_block_till_done()

    assert mock_config_entry.options[CONF_IGNORED_CHANNELS] == [1]

    freezer.tick(SCAN_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert 1 not in mock_config_entry.runtime_data.data
    assert 0 in mock_config_entry.runtime_data.data
