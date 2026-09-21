"""Tests for the Profalux Neosol cover platform."""

from unittest.mock import MagicMock, patch

from freezegun.api import FrozenDateTimeFactory
from pyneosol import Action, Channel, TransportError
import pytest
from syrupy.assertion import SnapshotAssertion

from homeassistant.components.cover import DOMAIN as COVER_DOMAIN, CoverState
from homeassistant.components.neosol.coordinator import SCAN_INTERVAL
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_CLOSE_COVER,
    SERVICE_OPEN_COVER,
    SERVICE_STOP_COVER,
    STATE_UNAVAILABLE,
    Platform,
)
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from . import setup_integration
from .conftest import CHANNELS

from tests.common import (
    MockConfigEntry,
    async_fire_time_changed,
    mock_restore_cache,
    snapshot_platform,
)

ENTITY_ID = "cover.shutter_0"
OTHER_ENTITY_ID = "cover.shutter_1"


@pytest.mark.usefixtures("mock_dongle")
async def test_entities(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    mock_config_entry: MockConfigEntry,
    snapshot: SnapshotAssertion,
) -> None:
    """Test one cover entity is created per paired channel."""
    with patch("homeassistant.components.neosol.PLATFORMS", [Platform.COVER]):
        await setup_integration(hass, mock_config_entry)

    await snapshot_platform(hass, entity_registry, snapshot, mock_config_entry.entry_id)


@pytest.mark.parametrize(
    ("service", "action", "expected_state"),
    [
        pytest.param(SERVICE_OPEN_COVER, Action.OPEN, CoverState.OPEN, id="open"),
        pytest.param(SERVICE_CLOSE_COVER, Action.CLOSE, CoverState.CLOSED, id="close"),
        pytest.param(SERVICE_STOP_COVER, Action.STOP, CoverState.OPEN, id="stop"),
    ],
)
async def test_commands(
    hass: HomeAssistant,
    mock_dongle: MagicMock,
    mock_config_entry: MockConfigEntry,
    service: str,
    action: Action,
    expected_state: str,
) -> None:
    """Test each command transmits on the right channel.

    The shutter is opened first, so that stopping has a state to leave untouched.
    """
    await setup_integration(hass, mock_config_entry)
    await hass.services.async_call(
        COVER_DOMAIN, SERVICE_OPEN_COVER, {ATTR_ENTITY_ID: ENTITY_ID}, blocking=True
    )
    mock_dongle.send.reset_mock()

    await hass.services.async_call(
        COVER_DOMAIN, service, {ATTR_ENTITY_ID: ENTITY_ID}, blocking=True
    )

    mock_dongle.send.assert_awaited_once_with(0, action)
    assert hass.states.get(ENTITY_ID).state == expected_state


async def test_command_failure(
    hass: HomeAssistant, mock_dongle: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """Test a transmission failure surfaces as a translated error."""
    await setup_integration(hass, mock_config_entry)
    mock_dongle.send.side_effect = TransportError("link died")

    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            COVER_DOMAIN, SERVICE_OPEN_COVER, {ATTR_ENTITY_ID: ENTITY_ID}, blocking=True
        )

    assert err.value.translation_key == "send_failed"


@pytest.mark.usefixtures("mock_dongle")
async def test_restores_assumed_state(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Test the state assumed before a restart is restored."""
    mock_restore_cache(hass, [State(ENTITY_ID, CoverState.CLOSED)])

    await setup_integration(hass, mock_config_entry)

    assert hass.states.get(ENTITY_ID).state == CoverState.CLOSED


async def test_newly_paired_channel_is_added(
    hass: HomeAssistant,
    mock_dongle: MagicMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test a channel paired after setup shows up on the next refresh."""
    await setup_integration(hass, mock_config_entry)
    assert hass.states.get("cover.shutter_2") is None

    mock_dongle.used_channels.return_value = [
        *CHANNELS,
        Channel(index=2, serial="000AAAA3", sync=1, key="00112233445566AA"),
    ]
    freezer.tick(SCAN_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert hass.states.get("cover.shutter_2") is not None


async def test_dropped_channel_becomes_unavailable(
    hass: HomeAssistant,
    mock_dongle: MagicMock,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    """Test a channel the dongle stops exposing is reported as unavailable."""
    await setup_integration(hass, mock_config_entry)
    assert hass.states.get(OTHER_ENTITY_ID).state != STATE_UNAVAILABLE

    mock_dongle.used_channels.return_value = CHANNELS[:1]
    freezer.tick(SCAN_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert hass.states.get(OTHER_ENTITY_ID).state == STATE_UNAVAILABLE
    assert hass.states.get(ENTITY_ID).state != STATE_UNAVAILABLE
