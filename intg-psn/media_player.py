"""
PlayStation Network Media Player entity for Unfolded Circle Remote Two.

:copyright: (c) 2025 by Jack Powell.
:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import logging
from typing import Any

from const import PSNConfig
from psn import PSNAccount
from ucapi import MediaPlayer as UCMediaPlayer
from ucapi import StatusCodes, media_player

_LOG = logging.getLogger(__name__)


class PSNMediaPlayer(UCMediaPlayer):  # pylint: disable=too-few-public-methods
    """Media player entity for PlayStation Network."""

    def __init__(self, device_config: PSNConfig, device: PSNAccount):
        """
        Initialize PSN media player entity.

        :param device_config: PSN device configuration
        :param device: PSN account device interface
        """
        entity_id = device_config.identifier

        features = [
            media_player.Features.MEDIA_TITLE,
            media_player.Features.MEDIA_ARTIST,
            media_player.Features.MEDIA_IMAGE_URL,
        ]

        attributes = {
            media_player.Attributes.STATE: media_player.States.UNKNOWN,
            media_player.Attributes.MEDIA_IMAGE_URL: "",
            media_player.Attributes.MEDIA_TITLE: "",
            media_player.Attributes.MEDIA_ARTIST: "",
        }

        super().__init__(
            entity_id,
            device_config.name,
            features,
            attributes,
            device_class=media_player.DeviceClasses.TV,
            options={},
        )

        self._device = device
        self._device_config = device_config

    async def command(
        self, cmd_id: str, params: dict[str, Any] | None = None
    ) -> StatusCodes:
        """
        Execute media player command.

        :param cmd_id: Command identifier
        :param params: Optional command parameters
        :return: Status code
        """
        _LOG.info(
            "Got %s command request: %s %s", self.id, cmd_id, params if params else ""
        )

        # PlayStation Network API doesn't currently support remote control commands
        # This would be where you'd implement play/pause/stop/volume commands
        # if the PSN API supported them in the future

        return StatusCodes.OK
