"""
PlayStation Network Game Title Sensor entity for Unfolded Circle Remote Two.

:copyright: (c) 2025 by Jack Powell.
:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import logging
from typing import Any

from const import PSNConfig
from psn import PSNAccount
from ucapi import StatusCodes, sensor
from ucapi.entity import EntityTypes
from ucapi_framework import create_entity_id
from ucapi_framework.entities import SensorEntity

_LOG = logging.getLogger(__name__)


class PSNSensor(SensorEntity):
    """Sensor entity that reports the currently playing game title."""

    def __init__(self, device_config: PSNConfig, device: PSNAccount):
        """
        Initialize PSN game title sensor entity.

        :param device_config: PSN device configuration
        :param device: PSN account device interface
        """
        entity_id = create_entity_id(EntityTypes.SENSOR, device_config.identifier)

        super().__init__(
            entity_id,
            f"{device_config.name} Game",
            features=[],
            attributes={
                sensor.Attributes.STATE: sensor.States.UNKNOWN,
                sensor.Attributes.VALUE: "",
            },
            device_class=sensor.DeviceClasses.CUSTOM,
            options={sensor.Options.CUSTOM_UNIT: "game"},
        )

        self._device: PSNAccount = device
        self._device_config = device_config

        self.subscribe_to_device(device)

    async def sync_state(self) -> None:
        """Sync sensor state from device to Remote."""
        from ucapi import media_player  # avoid circular at module level

        title = self._device.psn_media_title

        if self._device.psn_state == media_player.States.PLAYING and title:
            state = sensor.States.ON
        else:
            state = sensor.States.UNKNOWN

        self.update(
            {
                sensor.Attributes.STATE: state,
                sensor.Attributes.VALUE: title,
            }
        )
