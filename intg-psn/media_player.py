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
from ucapi.api_definitions import Pagination, Paging
from ucapi.entity import EntityTypes
from ucapi.media_player import BrowseMediaItem, BrowseResults
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
            features=[media_player.Features.BROWSE_MEDIA],
            attributes={
                media_player.Attributes.STATE: media_player.States.UNKNOWN,
                media_player.Attributes.MEDIA_IMAGE_URL: "",
                media_player.Attributes.MEDIA_TITLE: "",
                media_player.Attributes.MEDIA_ARTIST: "",
            },
            device_class=media_player.DeviceClasses.SPEAKER,
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

    async def browse(self, options: media_player.BrowseOptions) -> BrowseResults | StatusCodes:
        """
        Return a page of the user's played game library for the media browser.

        :param options: Browse options including paging (page, limit).
        :return: BrowseResults with game items, or SERVICE_UNAVAILABLE if disconnected.
        """
        if not self._device:
            return StatusCodes.SERVICE_UNAVAILABLE

        paging: Paging = options.paging
        titles, total = await self._device.get_game_library(
            limit=paging.limit, offset=paging.offset
        )

        items = [
            BrowseMediaItem(
                media_id=title.title_id or "",
                title=title.name or "Unknown",
                subtitle=title.last_played_date_time.strftime("%d %b %Y")
                if title.last_played_date_time
                else None,
                media_class=media_player.MediaClass.GAME,
                can_browse=False,
                can_play=False,
                thumbnail=str(title.image_url) if title.image_url else None,
            )
            for title in titles
        ]

        container = BrowseMediaItem(
            media_id="psn_games",
            title="Games",
            media_class=media_player.MediaClass.GAME,
            can_browse=True,
            items=items,
        )

        return BrowseResults(
            media=container,
            pagination=Pagination(
                page=paging.page,
                limit=len(items),
                count=total,
            ),
        )
