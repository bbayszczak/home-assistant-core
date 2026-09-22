"""Tests for the Profalux Neosol pairing flow."""

from datetime import timedelta
from unittest.mock import MagicMock, patch

from pyneosol import TransportError
import pytest

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from . import setup_integration
from .conftest import CHANNELS

from tests.common import MockConfigEntry

# The real window is a minute of the user performing a choreography on their remote.
NO_WAIT = patch(
    "homeassistant.components.neosol.config_flow.PAIRING_WINDOW", timedelta(0)
)


async def _async_run_pairing(
    hass: HomeAssistant, entry: MockConfigEntry
) -> dict[str, str]:
    """Walk through the pairing flow and return its final result."""
    with NO_WAIT:
        result = await hass.config_entries.options.async_init(entry.entry_id)
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "init"

        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {}
        )
        assert result["type"] is FlowResultType.SHOW_PROGRESS
        await hass.async_block_till_done()

        return await hass.config_entries.options.async_configure(result["flow_id"])


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
