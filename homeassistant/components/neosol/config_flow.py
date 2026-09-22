"""Config flow for the Profalux Neosol integration."""

import asyncio
from typing import Any, override

import probatio
from pyneosol import DongleInfo, NeosolError, NotADongleError

from homeassistant.components import usb
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_DEVICE
from homeassistant.core import callback
from homeassistant.helpers.selector import SerialPortSelector
from homeassistant.helpers.service_info.usb import UsbServiceInfo

from .const import DOMAIN, LOGGER, PAIRING_WINDOW
from .coordinator import NeosolConfigEntry, open_dongle

STEP_PORT_SCHEMA = probatio.Schema(
    {probatio.Required(CONF_DEVICE): SerialPortSelector()}
)

TITLE = "Profalux Neosol"


class NeosolConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Profalux Neosol."""

    VERSION = 1

    @staticmethod
    @callback
    @override
    def async_get_options_flow(config_entry: NeosolConfigEntry) -> OptionsFlow:
        """Return the flow that pairs a new shutter."""
        return NeosolOptionsFlow()

    _discovered_port: str
    _info: DongleInfo

    async def _async_probe(self, port: str) -> str | None:
        """Read the dongle on ``port``, keeping its info, or return an error key.

        The dongle is closed again right away: the config entry setup is what owns the
        serial port, and it can only be opened once.
        """
        try:
            dongle, info = await open_dongle(port)
        except NotADongleError:
            return "not_a_dongle"
        except NeosolError:
            return "cannot_connect"
        except Exception:  # noqa: BLE001
            LOGGER.exception("Unexpected exception")
            return "unknown"

        await dongle.close()
        self._info = info
        return None

    @override
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle a dongle configured by hand."""
        errors: dict[str, str] = {}

        if user_input is not None:
            port = await self.hass.async_add_executor_job(
                usb.get_serial_by_id, user_input[CONF_DEVICE]
            )

            if (error := await self._async_probe(port)) is None:
                await self.async_set_unique_id(self._info.serial_number)
                return self.async_create_entry(title=TITLE, data={CONF_DEVICE: port})

            errors["base"] = error

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                STEP_PORT_SCHEMA, user_input or {}
            ),
            errors=errors,
        )

    @override
    async def async_step_usb(self, discovery_info: UsbServiceInfo) -> ConfigFlowResult:
        """Handle a dongle plugged into the host."""
        port = await self.hass.async_add_executor_job(
            usb.get_serial_by_id, discovery_info.device
        )

        # The USB vendor id belongs to Silicon Labs and is shared by unrelated serial
        # adapters, so only the AT&V answer tells a dongle from anything else.
        if (error := await self._async_probe(port)) is not None:
            return self.async_abort(reason=error)

        await self.async_set_unique_id(self._info.serial_number)
        self._discovered_port = port
        self._set_confirm_only()
        return await self.async_step_usb_confirm()

    async def async_step_usb_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm the discovered dongle."""
        if user_input is not None:
            return self.async_create_entry(
                title=TITLE, data={CONF_DEVICE: self._discovered_port}
            )

        return self.async_show_form(step_id="usb_confirm")

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Point an existing entry at another serial port."""
        errors: dict[str, str] = {}

        if user_input is not None:
            port = await self.hass.async_add_executor_job(
                usb.get_serial_by_id, user_input[CONF_DEVICE]
            )

            if (error := await self._async_probe(port)) is None:
                await self.async_set_unique_id(self._info.serial_number)
                self._abort_if_unique_id_mismatch(reason="another_dongle")
                return self.async_update_reload_and_abort(
                    self._get_reconfigure_entry(), data_updates={CONF_DEVICE: port}
                )

            errors["base"] = error

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                STEP_PORT_SCHEMA, user_input or self._get_reconfigure_entry().data
            ),
            errors=errors,
        )


class NeosolOptionsFlow(OptionsFlow):
    """Pair a new shutter with a free channel of the dongle.

    This flow performs an action rather than storing settings: pairing is a timed
    choreography on the shutter's own remote, and the dongle only opens the window.
    """

    config_entry: NeosolConfigEntry

    _pairing_task: asyncio.Task[None] | None = None
    _error: str | None = None

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the choreography before opening the window."""
        if user_input is not None:
            return await self.async_step_pair()

        return self.async_show_form(
            step_id="init",
            description_placeholders={"seconds": str(PAIRING_WINDOW.seconds)},
        )

    async def async_step_pair(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Open the pairing window, and hold the flow until it closes."""
        if self._pairing_task is None:
            # Not eager: an attempt that fails at once would otherwise run to completion
            # before the first check, and the user would never see the window open.
            self._pairing_task = self.hass.async_create_task(
                self._async_pair(), eager_start=False
            )

        if not self._pairing_task.done():
            return self.async_show_progress(
                step_id="pair",
                progress_action="pairing",
                progress_task=self._pairing_task,
            )

        return self.async_show_progress_done(next_step_id="finish")

    async def async_step_finish(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Report how the attempt went."""
        return self.async_abort(reason=self._error or "pairing_finished")

    async def _async_pair(self) -> None:
        """Send the register frame on a free channel, then wait out the window.

        Nothing confirms the pairing: the motors never answer, and the dongle counts the
        register frame as a transmission whatever happens. The channel therefore shows up
        as a shutter either way, which the user can remove if the shutter does not obey.
        """
        coordinator = self.config_entry.runtime_data
        try:
            channels = await coordinator.dongle.channels()
        except NeosolError:
            self._error = "cannot_connect"
            return

        free = next((channel for channel in channels if not channel.is_used), None)
        if free is None:
            self._error = "no_free_channel"
            return

        try:
            await coordinator.dongle.register(free.index)
        except NeosolError:
            self._error = "cannot_connect"
            return

        # The window stays open whether or not the choreography is performed, and the
        # dongle must not be asked anything until it closes.
        await asyncio.sleep(PAIRING_WINDOW.total_seconds())
        await coordinator.async_request_refresh()
