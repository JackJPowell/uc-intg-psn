"""
PlayStation Network Game Title Sensor entity for Unfolded Circle Remote Two.

:copyright: (c) 2025 by Jack Powell.
:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import logging
from const import PSNConfig
from psn import PSNAccount
from ucapi import sensor
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


class PSNAuthenticationSensor(SensorEntity):
    """Sensor entity that reports whether the NPSSO token is valid."""

    def __init__(self, device_config: PSNConfig, device: PSNAccount):
        """Initialize the NPSSO authentication-status sensor."""
        entity_id = create_entity_id(
            EntityTypes.SENSOR, f"{device_config.identifier}_authentication"
        )

        super().__init__(
            entity_id,
            f"{device_config.name} NPSSO Token",
            features=[],
            attributes={
                sensor.Attributes.STATE: sensor.States.UNKNOWN,
                sensor.Attributes.VALUE: "Checking",
            },
            device_class=sensor.DeviceClasses.CUSTOM,
            options={},
        )

        self._device: PSNAccount = device
        self._device_config = device_config

        self.subscribe_to_device(device)

    async def sync_state(self) -> None:
        """Sync NPSSO authentication state from the device."""
        authenticated = self._device.psn_authenticated
        if authenticated is True:
            state = sensor.States.ON
            value = "Valid"
        elif authenticated is False:
            state = sensor.States.ON
            value = "Invalid - update NPSSO token"
        else:
            state = sensor.States.UNKNOWN
            value = "Checking"

        self.update(
            {
                sensor.Attributes.STATE: state,
                sensor.Attributes.VALUE: value,
            }
        )
