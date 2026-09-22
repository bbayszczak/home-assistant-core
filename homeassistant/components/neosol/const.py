"""Constants for the Profalux Neosol integration."""

from datetime import timedelta
import logging

DOMAIN = "neosol"
LOGGER = logging.getLogger(__package__)

MANUFACTURER = "Profalux"

# Channels the user removed from Home Assistant. A pairing attempt counts as a
# transmission, so a failed one leaves its channel looking paired forever; this is how
# the resulting phantom shutter is kept out.
CONF_IGNORED_CHANNELS = "ignored_channels"

#: How long the dongle keeps the pairing window open after the register frame.
PAIRING_WINDOW = timedelta(seconds=60)
