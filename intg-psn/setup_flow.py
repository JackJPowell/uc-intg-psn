"""
Setup flow for PlayStation Network integration.

:copyright: (c) 2023-2024
:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import logging
import os
import sys
from typing import Any

# Add parent directory to path for ucapi_base module (before it's published)
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PARENT_DIR = os.path.dirname(_SCRIPT_DIR)
if _PARENT_DIR not in sys.path:
    sys.path.insert(0, _PARENT_DIR)

import config  # noqa: E402
from config import PSNDevice  # noqa: E402
from psn import PlaystationNetwork  # noqa: E402
from psnawp_api.utils.misc import parse_npsso_token  # noqa: E402
from ucapi import RequestUserInput, SetupDriver  # noqa: E402
from ucapi_base import BaseSetupFlow  # noqa: E402

_LOG = logging.getLogger(__name__)


class PSNSetupFlow(BaseSetupFlow[PSNDevice]):
    """
    Setup flow for PlayStation Network integration.

    Handles PSN account configuration through NPSSO token authentication.
    PSN does not support device discovery, so manual entry is always required.
    """

    def __init__(self, config_manager):
        """
        Initialize PSN setup flow.

        :param config_manager: PSNDeviceManager instance
        """
        # PSN has no discovery, pass None for discovery_class
        super().__init__(config_manager, discovery_class=None)

    async def discover_devices(self) -> list[Any]:
        """
        PSN does not support device discovery.

        :return: Empty list (no discovery available)
        """
        return []

    async def create_device_from_discovery(
        self, device_id: str, additional_data: dict[str, Any]
    ) -> PSNDevice:
        """
        Not used for PSN (no discovery support).

        :param device_id: Device identifier
        :param additional_data: Additional data
        :return: PSN device configuration
        :raises NotImplementedError: PSN doesn't support discovery
        """
        raise NotImplementedError("PSN does not support device discovery")

    async def create_device_from_manual_entry(
        self, input_values: dict[str, Any]
    ) -> PSNDevice:
        """
        Create PSN device configuration from manual NPSSO token entry.

        :param input_values: User input containing 'npsso' token
        :return: PSN device configuration
        :raises ValueError: If authentication fails
        """
        npsso = parse_npsso_token(input_values.get("npsso", ""))

        if not npsso:
            _LOG.error("Invalid or missing NPSSO token")
            raise ValueError("Invalid or missing NPSSO token")

        try:
            _LOG.debug("Connecting to PSN API")
            psnawp = PlaystationNetwork(npsso)
            user = psnawp.get_user()
            _LOG.info("Authenticated PSN Account: %s", user.online_id)

            return PSNDevice(
                identifier=user.account_id, name=user.online_id, npsso=npsso
            )

        except Exception as err:
            _LOG.error("Failed to authenticate with PSN: %s", err)
            raise ValueError(f"Failed to authenticate with PSN: {err}") from err

    def get_manual_entry_form(self) -> RequestUserInput:
        """
        Get the NPSSO token entry form.

        :return: RequestUserInput for NPSSO token
        """
        return RequestUserInput(
            {"en": "PlayStation Network Setup"},
            [
                {
                    "id": "info",
                    "label": {
                        "en": "Supply your NPSSO Token",
                    },
                    "field": {
                        "label": {
                            "value": {
                                "en": (
                                    "Your NPSSO Token is required to authenticate to the PlayStation Network. "
                                    "\n\nPlease sign in to the [PlayStation Network](https://playstation.com) first. "
                                    "Then [click here](https://ca.account.sony.com/api/v1/ssocookie) to retrieve your token."
                                ),
                            }
                        }
                    },
                },
                {
                    "field": {"text": {"value": ""}},
                    "id": "npsso",
                    "label": {
                        "en": "NPSSO Token",
                    },
                },
            ],
        )

    def get_device_id(self, device_config: PSNDevice) -> str:
        """
        Extract device ID from PSN device configuration.

        :param device_config: PSN device configuration
        :return: Device identifier (account ID)
        """
        return device_config.identifier

    def get_device_name(self, device_config: PSNDevice) -> str:
        """
        Extract device name from PSN device configuration.

        :param device_config: PSN device configuration
        :return: Device name (online ID)
        """
        return device_config.name


# Global setup flow instance
_setup_flow: PSNSetupFlow | None = None


async def driver_setup_handler(msg: SetupDriver):
    """
    Entry point for driver setup requests.

    :param msg: Setup driver request object
    :return: Setup action on how to continue
    """
    global _setup_flow

    _LOG.info("=== SETUP HANDLER CALLED === msg type: %s", type(msg).__name__)
    _LOG.debug("Setup message: %s", msg)

    if _setup_flow is None:
        _LOG.info(
            "Creating new PSNSetupFlow instance with config.devices=%s", config.devices
        )
        _setup_flow = PSNSetupFlow(config.devices)

    result = await _setup_flow.handle_driver_setup(msg)
    _LOG.info("Setup handler returning: %s", type(result).__name__)
    return result
