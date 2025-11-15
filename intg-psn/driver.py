"""
PSN Integration Driver using ucapi_base framework.

:copyright: (c) 2023-2024 by Unfolded Circle ApS.
:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import asyncio
import logging
import os
import sys
from typing import Any

# Add parent directory to path for ucapi_base module (before it's published)
# This allows importing ucapi_base when running from intg-psn directory
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PARENT_DIR = os.path.dirname(_SCRIPT_DIR)
if _PARENT_DIR not in sys.path:
    sys.path.insert(0, _PARENT_DIR)

import setup_flow  # noqa: E402
import config  # noqa: E402
from config import PSNDevice  # noqa: E402
from media_player import PSNMediaPlayer  # noqa: E402
from psn import PSNAccount  # noqa: E402
from ucapi import media_player  # noqa: E402
from ucapi_base import BaseIntegrationDriver  # noqa: E402

_LOG = logging.getLogger(__name__)


class PSNIntegrationDriver(BaseIntegrationDriver[PSNAccount, PSNDevice]):
    """
    PSN Integration driver using ucapi_base framework.

    Handles PlayStation Network account management and entity lifecycle.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop):
        """
        Initialize PSN integration driver.

        :param loop: The asyncio event loop
        """
        super().__init__(
            loop=loop, device_class=PSNAccount, entity_classes=[PSNMediaPlayer]
        )
        self.config_manager: config.PSNDeviceManager | None = None

    # ========================================================================
    # Required Abstract Method Implementations
    # ========================================================================

    def device_from_entity_id(self, entity_id: str) -> str | None:
        """
        Extract device identifier from entity identifier.

        For PSN, the entity_id IS the device identifier (account_id).

        :param entity_id: Entity identifier
        :return: Device identifier (account_id)
        """
        return entity_id

    def get_entity_ids_for_device(self, device_id: str) -> list[str]:
        """
        Get all entity identifiers for a device.

        PSN has one media_player entity per account.

        :param device_id: Device identifier (account_id)
        :return: List containing single entity ID
        """
        return [device_id]

    def get_device_config(self, device_id: str) -> PSNDevice | None:
        """
        Get device configuration for the given device ID.

        :param device_id: Device identifier (account_id)
        :return: PSN device configuration or None
        """
        if self.config_manager is None:
            _LOG.error("Config manager not initialized")
            return None
        return self.config_manager.get(device_id)

    def get_device_id(self, device_config: PSNDevice) -> str:
        """
        Extract device ID from device configuration.

        :param device_config: PSN device configuration
        :return: Device identifier (account_id)
        """
        return device_config.identifier

    def get_device_name(self, device_config: PSNDevice) -> str:
        """
        Extract device name from device configuration.

        :param device_config: PSN device configuration
        :return: Device name (online_id)
        """
        return device_config.name

    def get_device_address(self, device_config: PSNDevice) -> str:
        """
        Extract device address from device configuration.

        PSN doesn't have a physical address, return identifier.

        :param device_config: PSN device configuration
        :return: Device identifier
        """
        return device_config.identifier

    def map_device_state(self, device_state: Any) -> media_player.States:
        """
        Map PSN device state to ucapi media player state.

        :param device_state: PSN state string
        :return: Media player state
        """
        match device_state:
            case "ON" | "MENU":
                return media_player.States.ON
            case "OFF":
                return media_player.States.OFF
            case "PLAYING":
                return media_player.States.PLAYING
            case _:
                return media_player.States.UNKNOWN

    def create_entities(
        self, device_config: PSNDevice, device: PSNAccount
    ) -> list[PSNMediaPlayer]:
        """
        Create entity instances for a PSN device.

        :param device_config: PSN device configuration
        :param device: PSN device instance
        :return: List containing PSNMediaPlayer entity
        """
        return [PSNMediaPlayer(device_config, device)]

    # ========================================================================
    # Device Event Handler Overrides
    # ========================================================================

    async def on_device_update(
        self, device_id: str, update: dict[str, Any] | None
    ) -> None:
        """
        Handle PSN device state updates.

        :param device_id: Device identifier (account_id)
        :param update: Dictionary containing updated PSN properties
        """
        if update is None:
            _LOG.debug("[%s] No update data provided", device_id)
            return

        # Ensure this device is configured
        if device_id not in self._configured_devices:
            _LOG.debug("[%s] Ignoring update for unknown device", device_id)
            return

        entity_id = device_id
        attributes = {}

        # Map state
        if "state" in update:
            state = self.map_device_state(update["state"])
            attributes[media_player.Attributes.STATE] = state

        # Map media information
        if "artwork" in update:
            attributes[media_player.Attributes.MEDIA_IMAGE_URL] = update["artwork"]
        if "title" in update:
            attributes[media_player.Attributes.MEDIA_TITLE] = update["title"]
        if "artist" in update:
            attributes[media_player.Attributes.MEDIA_ARTIST] = update["artist"]

        # Clear media info when OFF
        if "state" in update and update["state"] == "OFF":
            attributes[media_player.Attributes.MEDIA_IMAGE_URL] = ""
            attributes[media_player.Attributes.MEDIA_ARTIST] = ""
            attributes[media_player.Attributes.MEDIA_TITLE] = ""

        # Update entity attributes
        if attributes:
            if self.api.configured_entities.contains(entity_id):
                self.api.configured_entities.update_attributes(entity_id, attributes)
            elif self.api.available_entities.contains(entity_id):
                self.api.available_entities.update_attributes(entity_id, attributes)

    # ========================================================================
    # Device Lifecycle Callbacks
    # ========================================================================

    def on_device_added(self, device: PSNDevice) -> None:
        """
        Handle a newly added device in the configuration.

        :param device: PSN device configuration
        """
        _LOG.debug("New device added: %s", device)
        self.add_configured_device(device, connect=False)

    def on_device_removed(self, device: PSNDevice | None) -> None:
        """
        Handle a removed device in the configuration.

        :param device: PSN device configuration or None (clear all)
        """
        if device is None:
            _LOG.debug("Configuration cleared, removing all PSN devices")
            self.clear_devices()
        else:
            _LOG.debug("Removing PSN device: %s", device.identifier)
            self.remove_device(device.identifier)


async def main():
    """Start the Remote Two integration driver."""
    logging.basicConfig()

    level = os.getenv("UC_LOG_LEVEL", "DEBUG").upper()
    logging.getLogger("psn").setLevel(level)
    logging.getLogger("driver").setLevel(level)
    logging.getLogger("config").setLevel(level)
    logging.getLogger("discover").setLevel(level)
    logging.getLogger("setup_flow").setLevel(level)

    # Create event loop and driver
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    driver = PSNIntegrationDriver(loop)

    # Initialize configuration manager with device callbacks
    config.devices = config.PSNDeviceManager(
        driver.api.config_dir_path, driver.on_device_added, driver.on_device_removed
    )
    driver.config_manager = config.devices

    # Load and register all configured devices
    for device in config.devices.all():
        driver.add_configured_device(device, connect=False)

    # Initialize the API with setup handler
    await driver.api.init("driver.json", setup_flow.driver_setup_handler)


if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(main())
    loop.run_forever()
