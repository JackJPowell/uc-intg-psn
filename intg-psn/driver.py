"""
PSN Integration Driver using ucapi_base framework.

:copyright: (c) 2025 by Jack Powell.
:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import asyncio
import logging
import os
from dataclasses import dataclass
from media_player import PSNMediaPlayer
from psn import PSNAccount
from setup_flow import PSNSetupFlow
from ucapi_framework import BaseIntegrationDriver, BaseDeviceManager

_LOG = logging.getLogger("driver")


@dataclass
class PSNDevice:
    """PSN Account device configuration."""

    identifier: str
    """Unique identifier of the device."""
    name: str
    """Friendly name of the device."""
    npsso: str
    """Credentials for different protocols."""


class PSNIntegrationDriver(BaseIntegrationDriver[PSNAccount, PSNDevice]):
    """
    PSN Integration driver using ucapi_base framework.

    Handles PlayStation Network account management and entity lifecycle.
    """

    def device_from_entity_id(self, entity_id: str) -> str | None:
        """
        Extract device identifier from entity identifier.

        For PSN, the entity_id IS the device identifier (account_id).

        :param entity_id: Entity identifier
        :return: Device identifier (account_id)
        """
        # For PSN, the entity_id IS the device identifier (account_id).
        # It is not prefixed with media_player
        return entity_id

    # ========================================================================
    # Device Event Handler Overrides
    # ========================================================================

    # async def on_device_update(
    #     self, device_id: str, update: dict[str, Any] | None
    # ) -> None:
    #     """
    #     Handle PSN device state updates.

    #     :param device_id: Device identifier (account_id)
    #     :param update: Dictionary containing updated PSN properties
    #     """
    #     if update is None:
    #         _LOG.debug("[%s] No update data provided", device_id)
    #         return

    #     # Ensure this device is configured
    #     if device_id not in self._configured_devices:
    #         _LOG.debug("[%s] Ignoring update for unknown device", device_id)
    #         return

    #     entity_id = device_id
    #     attributes = {}

    #     # Map state
    #     if "state" in update:
    #         state = self.map_device_state(update["state"])
    #         attributes[media_player.Attributes.STATE] = state

    #     # Map media information
    #     if "artwork" in update:
    #         attributes[media_player.Attributes.MEDIA_IMAGE_URL] = update["artwork"]
    #     if "title" in update:
    #         attributes[media_player.Attributes.MEDIA_TITLE] = update["title"]
    #     if "artist" in update:
    #         attributes[media_player.Attributes.MEDIA_ARTIST] = update["artist"]

    #     # Clear media info when OFF
    #     if "state" in update and update["state"] == "OFF":
    #         attributes[media_player.Attributes.MEDIA_IMAGE_URL] = ""
    #         attributes[media_player.Attributes.MEDIA_ARTIST] = ""
    #         attributes[media_player.Attributes.MEDIA_TITLE] = ""

    #     # Update entity attributes
    #     if attributes:
    #         if self.api.configured_entities.contains(entity_id):
    #             self.api.configured_entities.update_attributes(entity_id, attributes)
    #         elif self.api.available_entities.contains(entity_id):
    #             self.api.available_entities.update_attributes(entity_id, attributes)


async def main():
    """Start the Remote Two integration driver."""
    logging.basicConfig()

    level = os.getenv("UC_LOG_LEVEL", "DEBUG").upper()
    logging.getLogger("psn").setLevel(level)
    logging.getLogger("driver").setLevel(level)
    logging.getLogger("setup_flow").setLevel(level)

    loop = asyncio.get_running_loop()

    driver = PSNIntegrationDriver(
        loop=loop, device_class=PSNAccount, entity_classes=[PSNMediaPlayer]
    )

    driver.config = BaseDeviceManager(
        driver.api.config_dir_path,
        driver.on_device_added,
        driver.on_device_removed,
        device_class=PSNDevice,
    )

    for device in list(driver.config.all()):
        driver.add_configured_device(device, connect=False)

    setup_handler = PSNSetupFlow.create_handler(driver.config)

    await driver.api.init("driver.json", setup_handler)

    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(main())
