"""
PSN Integration Driver.

:copyright: (c) 2025 by Jack Powell.
:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import asyncio
import logging
import os

from const import PSNConfig
from media_player import PSNMediaPlayer
from psn import PSNAccount
from setup_flow import PSNSetupFlow
from ucapi_framework import BaseConfigManager, BaseIntegrationDriver, get_config_path

_LOG = logging.getLogger("driver")


class PSNIntegrationDriver(BaseIntegrationDriver[PSNAccount, PSNConfig]):
    """
    PSN Integration driver.

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


async def main():
    """Start the Remote Two integration driver."""
    logging.basicConfig()

    level = os.getenv("UC_LOG_LEVEL", "DEBUG").upper()
    logging.getLogger("psn").setLevel(level)
    logging.getLogger("driver").setLevel(level)
    logging.getLogger("setup_flow").setLevel(level)

    driver = PSNIntegrationDriver(
        device_class=PSNAccount, entity_classes=[PSNMediaPlayer], driver_id="psn_driver"
    )

    driver.config_manager = BaseConfigManager(
        get_config_path(driver.api.config_dir_path),
        driver.on_device_added,
        driver.on_device_removed,
        config_class=PSNConfig,
    )

    await driver.register_all_configured_devices()

    setup_handler = PSNSetupFlow.create_handler(driver)
    await driver.api.init("driver.json", setup_handler)

    await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
