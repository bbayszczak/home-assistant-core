"""Constants for the Profalux Neosol integration."""

from datetime import timedelta
import logging

DOMAIN = "neosol"
LOGGER = logging.getLogger(__package__)

MANUFACTURER = "Profalux"

# Channels a check movement showed no shutter obeys: a failed pairing, an unpairing, or
# a forgotten shutter. The dongle counts a channel as used once it transmitted, so this
# is what keeps such a channel from coming back as a shutter, and what frees it for the
# next pairing.
CONF_IGNORED_CHANNELS = "ignored_channels"

#: How long a motor waits for the remote sequence after a register or unregister frame.
SEQUENCE_WINDOW = timedelta(seconds=60)

# The check movement goes up, then down, so that a shutter resting against either end
# stop still visibly moves if it obeys the channel.
NUDGE_UP_DURATION = timedelta(seconds=3)
NUDGE_DOWN_DURATION = timedelta(seconds=2)
