"""Tests for the Profalux Neosol pairing and unpairing flow."""

from datetime import timedelta
from unittest.mock import MagicMock, patch

from pyneosol import TransportError
import pytest

from homeassistant.components.neosol.const import CONF_IGNORED_CHANNELS, DOMAIN
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from homeassistant.helpers import device_registry as dr
from homeassistant.setup import async_setup_component

from . import MOCK_SERIAL, setup_integration
from .conftest import CHANNELS

from tests.common import MockConfigEntry
from tests.typing import WebSocketGenerator

# The real window is a minute of the user performing a choreography on their remote.
NO_WAIT = patch(
    "homeassistant.components.neosol.config_flow.PAIRING_WINDOW", timedelta(0)
)

UNPAIRED_SHUTTER = (DOMAIN, f"{MOCK_SERIAL}_1")


async def _async_open_menu_option(
    hass: HomeAssistant, entry: MockConfigEntry, option: str
) -> ConfigFlowResult:
    """Start the options flow and pick ``option`` from its menu."""
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "init"

    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": option}
    )


async def _async_run_pairing(
    hass: HomeAssistant, entry: MockConfigEntry
) -> ConfigFlowResult:
    """Walk through the pairing flow and return its final result."""
    with NO_WAIT:
        result = await _async_open_menu_option(hass, entry, "pair")
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "pair"

        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {}
        )
        assert result["type"] is FlowResultType.SHOW_PROGRESS
        await hass.async_block_till_done()

        return await hass.config_entries.options.async_configure(result["flow_id"])


async def _async_start_unpairing(
    hass: HomeAssistant, entry: MockConfigEntry
) -> ConfigFlowResult:
    """Pick shutter 1 in the unpairing flow, which sends the unpairing frame."""
    result = await _async_open_menu_option(hass, entry, "unpair")
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "unpair"

    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"shutter": "1"}
    )


async def test_pairing_flow(
    hass: HomeAssistant, mock_dongle: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """Test the window is opened on the first channel that never transmitted."""
    await setup_integration(hass, mock_config_entry)

    result = await _async_run_pairing(hass, mock_config_entry)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "pairing_finished"
    mock_dongle.register.assert_awaited_once_with(2)


async def test_pairing_without_a_free_channel(
    hass: HomeAssistant, mock_dongle: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """Test a dongle whose fifty channels have all transmitted."""
    await setup_integration(hass, mock_config_entry)
    mock_dongle.channels.return_value = list(CHANNELS)

    result = await _async_run_pairing(hass, mock_config_entry)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_free_channel"
    mock_dongle.register.assert_not_awaited()


@pytest.mark.parametrize("failing_call", ["channels", "register"])
async def test_pairing_when_the_dongle_fails(
    hass: HomeAssistant,
    mock_dongle: MagicMock,
    mock_config_entry: MockConfigEntry,
    failing_call: str,
) -> None:
    """Test a dongle that stops answering during the pairing flow."""
    await setup_integration(hass, mock_config_entry)
    getattr(mock_dongle, failing_call).side_effect = TransportError("link died")

    result = await _async_run_pairing(hass, mock_config_entry)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "cannot_connect"


async def test_unpairing_confirmed(
    hass: HomeAssistant,
    device_registry: dr.DeviceRegistry,
    mock_dongle: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test a shutter the user saw confirm is removed, and kept out afterwards."""
    await setup_integration(hass, mock_config_entry)

    result = await _async_start_unpairing(hass, mock_config_entry)

    mock_dongle.unregister.assert_awaited_once_with(1)
    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "unpair_confirm"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "unpair_confirmed"}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "unpaired"
    # The dongle still counts the channel as used, so only this keeps it out.
    assert mock_config_entry.options[CONF_IGNORED_CHANNELS] == [1]
    assert (
        device_registry.async_get_device_by_identifier(
            UNPAIRED_SHUTTER, mock_config_entry.entry_id
        )
        is None
    )
    assert hass.states.get("cover.shutter_1") is None


@pytest.mark.usefixtures("mock_dongle")
async def test_unpairing_not_confirmed(
    hass: HomeAssistant,
    device_registry: dr.DeviceRegistry,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test a shutter that did not confirm is left in place, since it still obeys."""
    await setup_integration(hass, mock_config_entry)

    result = await _async_start_unpairing(hass, mock_config_entry)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "unpair_not_confirmed"}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "still_paired"
    assert CONF_IGNORED_CHANNELS not in mock_config_entry.options
    assert device_registry.async_get_device_by_identifier(
        UNPAIRED_SHUTTER, mock_config_entry.entry_id
    )


async def test_unpairing_when_the_dongle_fails(
    hass: HomeAssistant, mock_dongle: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """Test a failed unpairing frame is reported, and can be sent again."""
    await setup_integration(hass, mock_config_entry)
    mock_dongle.unregister.side_effect = TransportError("link died")

    result = await _async_start_unpairing(hass, mock_config_entry)

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}

    mock_dongle.unregister.side_effect = None
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"shutter": "1"}
    )

    assert result["type"] is FlowResultType.MENU
    assert result["step_id"] == "unpair_confirm"


async def test_unpairing_without_shutters(
    hass: HomeAssistant, mock_dongle: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """Test there is nothing to pick when the dongle exposes no shutter."""
    mock_dongle.used_channels.return_value = []
    await setup_integration(hass, mock_config_entry)

    result = await _async_open_menu_option(hass, mock_config_entry, "unpair")

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_shutters"


@pytest.mark.usefixtures("mock_dongle")
async def test_a_deleted_shutter_cannot_be_unpaired(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    device_registry: dr.DeviceRegistry,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Test a shutter deleted since the last refresh is no longer offered."""
    assert await async_setup_component(hass, "config", {})
    await setup_integration(hass, mock_config_entry)
    device = device_registry.async_get_device_by_identifier(
        UNPAIRED_SHUTTER, mock_config_entry.entry_id
    )
    client = await hass_ws_client(hass)
    assert (await client.remove_device(device.id))["success"]

    result = await _async_open_menu_option(hass, mock_config_entry, "unpair")

    with pytest.raises(InvalidData):
        await hass.config_entries.options.async_configure(
            result["flow_id"], {"shutter": "1"}
        )
