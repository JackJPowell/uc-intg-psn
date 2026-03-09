"""
PlayStation Network Media Player entity for Unfolded Circle Remote Two.

:copyright: (c) 2025 by Jack Powell.
:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import logging
from typing import Any

from const import PSNConfig
from psn import PSNAccount
from ucapi import StatusCodes, media_player
from ucapi.entity import EntityTypes
from ucapi_framework import create_entity_id
from ucapi_framework.entities import MediaPlayerEntity

_LOG = logging.getLogger(__name__)


class PSNMediaPlayer(MediaPlayerEntity):
    """Media player entity for PlayStation Network."""

    def __init__(self, device_config: PSNConfig, device: PSNAccount):
        """
        Initialize PSN media player entity.

        :param device_config: PSN device configuration
        :param device: PSN account device interface
        """
        entity_id = create_entity_id(EntityTypes.MEDIA_PLAYER, device_config.identifier)

        super().__init__(
            entity_id,
            device_config.name,
            features=[],
            attributes={
                media_player.Attributes.STATE: media_player.States.UNKNOWN,
                media_player.Attributes.MEDIA_IMAGE_URL: "",
                media_player.Attributes.MEDIA_TITLE: "",
                media_player.Attributes.MEDIA_ARTIST: "",
            },
            device_class=media_player.DeviceClasses.TV,
            options={},
        )

        self._device: PSNAccount = device
        self._device_config = device_config

        # Subscribe so sync_state() is called on every push_update()
        self.subscribe_to_device(device)

    async def sync_state(self) -> None:
        """Sync entity state from device to Remote."""
        self.update(
            {
                media_player.Attributes.STATE: self._device.psn_state,
                media_player.Attributes.MEDIA_TITLE: self._device.psn_media_title,
                media_player.Attributes.MEDIA_ARTIST: self._device.psn_media_artist,
                media_player.Attributes.MEDIA_IMAGE_URL: self._device.psn_media_image_url,
            }
        )

    async def command(
        self, cmd_id: str, params: dict[str, Any] | None = None, *, websocket: Any
    ) -> StatusCodes:
        """
        Execute media player command.

        :param cmd_id: Command identifier
        :param params: Optional command parameters
        :return: Status code
        """
        if not self._device:
            return StatusCodes.SERVICE_UNAVAILABLE

        _LOG.info(
            "Got %s command request: %s %s", self.id, cmd_id, params if params else ""
        )

        # PlayStation Network API doesn't currently support remote control commands
        # This would be where you'd implement play/pause/stop/volume commands
        # if the PSN API supported them in the future

        return StatusCodes.OK
