"""
PlayStation Network Game Title Sensor entity for Unfolded Circle Remote Two.

:copyright: (c) 2025 by Jack Powell.
:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import logging
from typing import ClassVar

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
            value = "Checking" if self._device.has_psn else "Not configured"

        self.update(
            {
                sensor.Attributes.STATE: state,
                sensor.Attributes.VALUE: value,
            }
        )


class PSNDiagnosticSensor(SensorEntity):
    """Additional metrics share the coordinator's data and have no network I/O."""

    TYPES: ClassVar[dict[str, tuple[str, str]]] = {
        "active_title_id": ("Active Title ID", ""),
        "connection": ("Remote Play Connection Status", ""),
        "psn_health": ("PSN API Health", ""),
        "installed_titles": ("Installed Titles", "titles"),
        "trophy_level": ("Trophy Level", "level"),
        "earned_trophies": ("Earned Trophies", "trophies"),
        "platinum_trophies": ("Platinum Trophies", "trophies"),
        "current_game_trophies": ("Current Game Trophy Progress", "%"),
    }

    def __init__(self, config, device, kind):
        label, unit = self.TYPES[kind]
        super().__init__(
            create_entity_id(EntityTypes.SENSOR, config.identifier, kind),
            f"{config.name} {label}",
            features=[],
            attributes={
                sensor.Attributes.STATE: sensor.States.UNKNOWN,
                sensor.Attributes.VALUE: "",
            },
            device_class=sensor.DeviceClasses.CUSTOM,
            options={sensor.Options.CUSTOM_UNIT: unit} if unit else {},
        )
        self._device, self._kind = device, kind
        self.subscribe_to_device(device)

    async def sync_state(self) -> None:
        device, kind = self._device, self._kind
        summary = device.trophy_summary or {}
        values = {
            "active_title_id": device.psn_media_title_id or None,
            "connection": device.connection_status,
            "psn_health": device.api_health,
            "installed_titles": device.installed_count
            if device.installed_inventory_available
            else None,
            "trophy_level": summary.get("level"),
            "earned_trophies": summary.get("earned"),
            "platinum_trophies": summary.get("platinum"),
            "current_game_trophies": device.active_trophy_progress,
        }
        value = values[kind]
        self.update(
            {
                sensor.Attributes.STATE: sensor.States.ON
                if value is not None
                else sensor.States.UNKNOWN,
                sensor.Attributes.VALUE: value if value is not None else "",
            }
        )
