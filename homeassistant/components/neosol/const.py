"""Constants for the Profalux Neosol integration."""

from datetime import timedelta
import logging

DOMAIN = "neosol"
LOGGER = logging.getLogger(__package__)

MANUFACTURER = "Profalux"

# Channels the user removed or unpaired. The dongle counts a channel as used once it
# transmitted, whether the pairing failed or the shutter was unpaired since, so this is
# how such a channel is kept from coming back as a shutter.
CONF_IGNORED_CHANNELS = "ignored_channels"

#: How long a motor waits for the remote sequence after a register or unregister frame.
SEQUENCE_WINDOW = timedelta(seconds=60)

#: How long the shutter moves when checking whether it obeys a channel. Long enough to
#: be seen, short enough to stop well before an end stop.
NUDGE_DURATION = timedelta(seconds=2)
