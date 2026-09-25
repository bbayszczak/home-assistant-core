"""Config flow for the Profalux Neosol integration."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, override

import probatio
from pyneosol import Action, DongleInfo, NeosolError, NotADongleError

from homeassistant.components import usb
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_DEVICE
from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SerialPortSelector,
)
from homeassistant.helpers.service_info.usb import UsbServiceInfo

from .const import (
    DOMAIN,
    LOGGER,
    NUDGE_DOWN_DURATION,
    NUDGE_UP_DURATION,
    SEQUENCE_WINDOW,
)
from .coordinator import NeosolConfigEntry, open_dongle

STEP_PORT_SCHEMA = probatio.Schema(
    {probatio.Required(CONF_DEVICE): SerialPortSelector()}
)

TITLE = "Profalux Neosol"

CONF_SHUTTER = "shutter"


class NeosolConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Profalux Neosol."""

    VERSION = 1

    @staticmethod
    @callback
    @override
    def async_get_options_flow(config_entry: NeosolConfigEntry) -> OptionsFlow:
        """Return the flow that pairs, unpairs and forgets shutters."""
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
    """Pair a new shutter with a free channel of the dongle, unpair one, or forget one.

    This flow performs actions rather than storing settings. Nothing confirms any of
    them: the motors never answer, and the dongle counts every frame as a transmission
    whatever happens. So each ends by moving the shutter briefly on its channel, and the
    user, who watches it, tells whether it obeyed.

    It is also the only way out of Home Assistant for a shutter: the device page offers
    no delete, because a shutter deleted while it still obeys would free a channel that
    the next pairing would then share with it.
    """

    config_entry: NeosolConfigEntry

    _task: asyncio.Task[None] | None = None
    _error: str | None = None
    _channel: int

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user pick between pairing, unpairing and forgetting."""
        return self.async_show_menu(
            step_id="init", menu_options=["pair", "unpair", "forget"]
        )

    async def async_step_pair(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the choreography before opening the window."""
        if user_input is not None:
            return await self.async_step_pair_window()

        return self.async_show_form(
            step_id="pair",
            description_placeholders={"seconds": str(SEQUENCE_WINDOW.seconds)},
        )

    async def async_step_pair_window(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Open the pairing window, and hold the flow until the check is done."""
        return self._async_hold(
            "pair_window", "pairing", self._async_pair, "pair_check"
        )

    async def async_step_pair_check(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask whether the shutter moved on the check."""
        return self._async_show_check("pair_check", ["pair_moved", "pair_not_moved"])

    async def async_step_pair_moved(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Bring in the new shutter, which obeys its channel."""
        await self.config_entry.runtime_data.async_request_refresh()
        return self.async_abort(reason="paired")

    async def async_step_pair_not_moved(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Keep out the channel, which no shutter obeys."""
        self._async_forget_shutter()
        return self.async_abort(reason="not_paired")

    async def _async_pair(self) -> None:
        """Send the register frame on a free channel, wait out the window, then check."""
        coordinator = self.config_entry.runtime_data
        channels = await coordinator.dongle.channels()

        # A channel is free when no shutter in Home Assistant uses it: it either never
        # transmitted, or a check showed no shutter obeys it any more.
        ignored = coordinator.ignored_channels
        free = next(
            (
                channel
                for channel in channels
                if not channel.is_used or channel.index in ignored
            ),
            None,
        )
        if free is None:
            self._error = "no_free_channel"
            return

        self._channel = free.index
        # Should the user leave before answering, the channel must show up as a shutter
        # like a never used one, rather than stay free while a shutter may obey it.
        coordinator.async_unignore_channel(free.index)
        await coordinator.dongle.register(free.index)

        # The window stays open whether or not the choreography is performed, and the
        # dongle must not transmit until it closes.
        await asyncio.sleep(SEQUENCE_WINDOW.total_seconds())
        await self._async_nudge()

    async def async_step_unpair(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick the shutter to unpair, and send the frame that starts the unpairing."""
        errors: dict[str, str] = {}

        if user_input is not None:
            channel = int(user_input[CONF_SHUTTER])
            try:
                await self.config_entry.runtime_data.dongle.unregister(channel)
            except NeosolError:
                errors["base"] = "cannot_connect"
            else:
                self._channel = channel
                return await self.async_step_unpair_window()

        return self._async_show_shutter_form("unpair", errors)

    async def async_step_unpair_window(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the choreography, and hold the flow until the check is done."""
        return self._async_hold(
            "unpair_window", "unpairing", self._async_check_unpairing, "unpair_check"
        )

    async def async_step_unpair_check(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask whether the shutter moved on the check."""
        return self._async_show_check(
            "unpair_check", ["unpair_moved", "unpair_not_moved"]
        )

    async def async_step_unpair_moved(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Leave the shutter in place, since it still obeys its channel."""
        return self.async_abort(reason="still_paired")

    async def async_step_unpair_not_moved(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Forget the shutter, which no longer obeys its channel."""
        self._async_forget_shutter()
        return self.async_abort(reason="unpaired")

    async def _async_check_unpairing(self) -> None:
        """Wait out the unpairing window, then check."""
        # Moving the shutter while its motor still waits for the sequence could disturb
        # the unpairing, so the check only comes once the window is over.
        await asyncio.sleep(SEQUENCE_WINDOW.total_seconds())
        await self._async_nudge()

    async def async_step_forget(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick the shutter to forget, for one that no longer obeys its channel."""
        if user_input is not None:
            self._channel = int(user_input[CONF_SHUTTER])
            return await self.async_step_forget_window()

        return self._async_show_shutter_form("forget", {})

    async def async_step_forget_window(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Hold the flow until the check is done."""
        return self._async_hold(
            "forget_window", "checking", self._async_nudge, "forget_check"
        )

    async def async_step_forget_check(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask whether the shutter moved on the check."""
        return self._async_show_check(
            "forget_check", ["forget_moved", "forget_not_moved"]
        )

    async def async_step_forget_moved(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Keep the shutter, which still obeys its channel and has to be unpaired."""
        return self.async_abort(reason="still_obeys")

    async def async_step_forget_not_moved(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Forget the shutter, which no longer obeys its channel."""
        self._async_forget_shutter()
        return self.async_abort(reason="forgotten")

    def _async_show_shutter_form(
        self, step_id: str, errors: dict[str, str]
    ) -> ConfigFlowResult:
        """Show a form to pick one of the shutters, by the name the user knows."""
        # A shutter forgotten or unpaired keeps its channel in the coordinator data until
        # the next refresh, but has no device left to be picked by.
        shutters = [
            SelectOptionDict(
                value=str(channel),
                label=device.name_by_user or device.name or str(channel),
            )
            for channel in sorted(self.config_entry.runtime_data.data)
            if (device := self._shutter_device(channel))
        ]
        if not shutters:
            return self.async_abort(reason="no_shutters")

        return self.async_show_form(
            step_id=step_id,
            data_schema=probatio.Schema(
                {
                    probatio.Required(CONF_SHUTTER): SelectSelector(
                        SelectSelectorConfig(options=shutters)
                    )
                }
            ),
            errors=errors,
            description_placeholders={"seconds": str(SEQUENCE_WINDOW.seconds)},
        )

    def _async_show_check(
        self, step_id: str, menu_options: list[str]
    ) -> ConfigFlowResult:
        """Ask whether the shutter moved, which only the user can see."""
        if self._error:
            return self.async_abort(reason=self._error)

        return self.async_show_menu(step_id=step_id, menu_options=menu_options)

    def _async_hold(
        self,
        step_id: str,
        progress_action: str,
        job: Callable[[], Awaitable[None]],
        next_step_id: str,
    ) -> ConfigFlowResult:
        """Run ``job`` in the background, and show ``progress_action`` until it is done."""
        if self._task is None:
            # Not eager: a job that fails at once would otherwise run to completion
            # before the first check, and the user would never see the progress.
            self._task = self.hass.async_create_task(
                self._async_run(job), eager_start=False
            )

        if not self._task.done():
            return self.async_show_progress(
                step_id=step_id,
                progress_action=progress_action,
                progress_task=self._task,
                description_placeholders={"seconds": str(SEQUENCE_WINDOW.seconds)},
            )

        return self.async_show_progress_done(next_step_id=next_step_id)

    async def _async_run(self, job: Callable[[], Awaitable[None]]) -> None:
        """Run ``job``, turning a dongle failure into the error the check reports."""
        try:
            await job()
        except NeosolError:
            self._error = "cannot_connect"

    async def _async_nudge(self) -> None:
        """Move the shutter up, then down, briefly on its channel, for the user to watch."""
        dongle = self.config_entry.runtime_data.dongle
        await dongle.send(self._channel, Action.OPEN)
        await asyncio.sleep(NUDGE_UP_DURATION.total_seconds())
        await dongle.send(self._channel, Action.STOP)
        await dongle.send(self._channel, Action.CLOSE)
        await asyncio.sleep(NUDGE_DOWN_DURATION.total_seconds())
        await dongle.send(self._channel, Action.STOP)

    def _shutter_device(self, channel: int) -> dr.DeviceEntry | None:
        """Return the device of the shutter on ``channel``, if Home Assistant has one."""
        serial = self.config_entry.runtime_data.info.serial_number
        return dr.async_get(self.hass).async_get_device_by_identifier(
            (DOMAIN, f"{serial}_{channel}"), self.config_entry.entry_id
        )

    def _async_forget_shutter(self) -> None:
        """Keep the channel out of the shutters, and remove its device if it has one."""
        self.config_entry.runtime_data.async_ignore_channel(self._channel)
        if device := self._shutter_device(self._channel):
            dr.async_get(self.hass).async_remove_device(device.id)
