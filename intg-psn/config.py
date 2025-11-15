"""
Configuration handling of the integration driver.

:copyright: (c) 2023-2024 by Unfolded Circle ApS.
:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import logging
import os
import sys
from dataclasses import dataclass

# Add parent directory to path for ucapi_base module (before it's published)
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PARENT_DIR = os.path.dirname(_SCRIPT_DIR)
if _PARENT_DIR not in sys.path:
    sys.path.insert(0, _PARENT_DIR)

from ucapi_base import BaseDeviceManager  # noqa: E402

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

    def get_device_id(self, device: PSNDevice) -> str:
        """Extract device ID from PSN device."""
        return device.identifier

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


# Global device manager instance (initialized in main)
devices: PSNDeviceManager | None = None
