"""
Configuration handling of the integration driver.

:copyright: (c) 2025 by Jack Powell.
:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import logging
from dataclasses import dataclass
from ucapi_framework import BaseDeviceManager


_LOG = logging.getLogger(__name__)


@dataclass
class PSNDevice:
    """PSN Account device configuration."""

    identifier: str
    """Unique identifier of the device."""
    name: str
    """Friendly name of the device."""
    npsso: str
    """Credentials for different protocols."""


class PSNDeviceManager(BaseDeviceManager[PSNDevice]):
    """Configuration manager for PSN Account devices."""

    def deserialize_device(self, data: dict) -> PSNDevice | None:
        """Deserialize PSN device from JSON."""
        try:
            return PSNDevice(
                identifier=data["identifier"],
                name=data.get("name", "PlayStation"),
                npsso=data["npsso"],
            )
        except (KeyError, TypeError) as ex:
            _LOG.error("Failed to deserialize PSN device: %s", ex)
            return None
