"""
PSN Integration Driver using ucapi_base framework.

:copyright: (c) 2025 by Jack Powell.
:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import asyncio
import logging
import os
from media_player import PSNMediaPlayer
from psn import PSNAccount
from setup_flow import PSNSetupFlow
from ucapi_framework import BaseIntegrationDriver, BaseDeviceManager, get_config_path
from const import PSNDevice

_LOG = logging.getLogger("driver")


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
        get_config_path(driver.api.config_dir_path),
        driver.on_device_added,
        driver.on_device_removed,
        device_class=PSNDevice,
    )

    for device in list(driver.config.all()):
        driver.add_configured_device(device)

    setup_handler = PSNSetupFlow.create_handler(driver.config)

    await driver.api.init("driver.json", setup_handler)

    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(main())
