"""
PlayStation Network Power Switch entity for Unfolded Circle Remote Two.

:copyright: (c) 2025 by Jack Powell.
:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import logging
from typing import Any

from const import PSNConfig
from psn import PSNAccount
from ucapi import StatusCodes, switch
from ucapi.entity import EntityTypes
from ucapi_framework import create_entity_id
from ucapi_framework.entities import SwitchEntity

_LOG = logging.getLogger(__name__)


class PSNSwitch(SwitchEntity):
    """Power switch entity for a paired PlayStation console."""

    def __init__(self, device_config: PSNConfig, device: PSNAccount):
        """
        Initialize PSN power switch entity.

        :param device_config: PSN device configuration
        :param device: PSN account device interface
        """
        entity_id = create_entity_id(EntityTypes.SWITCH, device_config.identifier)

        super().__init__(
            entity_id,
            f"{device_config.name} Power",
            features=[switch.Features.ON_OFF, switch.Features.TOGGLE],
            attributes={switch.Attributes.STATE: switch.States.UNKNOWN},
        )

        self._device: PSNAccount = device
        self._device_config = device_config

        self.subscribe_to_device(device)

    async def sync_state(self) -> None:
        """Sync switch state from device to Remote."""
        from ucapi import media_player  # avoid circular at module level

        if self._device.psn_state in (
            media_player.States.ON,
            media_player.States.PLAYING,
        ):
            state = switch.States.ON
        else:
            state = switch.States.OFF

        self.update({switch.Attributes.STATE: state})

    async def command(
        self, cmd_id: str, params: dict[str, Any] | None = None, *, websocket: Any
    ) -> StatusCodes:
        """
        Execute switch command.

        :param cmd_id: Command identifier (on/off/toggle)
        :param params: Optional command parameters
        :return: Status code
        """
        if not self._device:
            return StatusCodes.SERVICE_UNAVAILABLE

        _LOG.info(
            "Got %s command request: %s %s", self.id, cmd_id, params if params else ""
        )

        cmd = switch.Commands(cmd_id)

        if cmd == switch.Commands.ON:
            await self._device.power_on()
            return StatusCodes.OK
        if cmd == switch.Commands.OFF:
            await self._device.power_off()
            return StatusCodes.OK
        if cmd == switch.Commands.TOGGLE:
            await self._device.power_toggle()
            return StatusCodes.OK

        _LOG.warning("[%s] Unhandled command: %s", self.id, cmd_id)
        return StatusCodes.NOT_IMPLEMENTED
