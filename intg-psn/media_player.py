"""
PlayStation Network Media Player entity for Unfolded Circle Remote Two.

:copyright: (c) 2025 by Jack Powell.
:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import logging
from typing import Any

import playdirector
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

        features = [media_player.Features.BROWSE_MEDIA]
        if device.has_control:
            features += [media_player.Features.ON_OFF, media_player.Features.TOGGLE]
            if device.device_type == "PS5":
                features += [media_player.Features.HOME]

        super().__init__(
            entity_id,
            device_config.name,
            features=features,
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

        cmd = media_player.Commands(cmd_id)

        if cmd == media_player.Commands.ON:
            await self._device.power_on()
            return StatusCodes.OK
        if cmd == media_player.Commands.OFF:
            await self._device.power_off()
            return StatusCodes.OK
        if cmd == media_player.Commands.TOGGLE:
            await self._device.power_toggle()
            return StatusCodes.OK
        if cmd == media_player.Commands.HOME:
            await self._device.go_home()
            return StatusCodes.OK

        # PS4 dpad / button commands → RemoteOperation
        _PS4_BUTTON_MAP = {
            media_player.Commands.CURSOR_UP: playdirector.RemoteOperation.UP,
            media_player.Commands.CURSOR_DOWN: playdirector.RemoteOperation.DOWN,
            media_player.Commands.CURSOR_LEFT: playdirector.RemoteOperation.LEFT,
            media_player.Commands.CURSOR_RIGHT: playdirector.RemoteOperation.RIGHT,
            media_player.Commands.CURSOR_ENTER: playdirector.RemoteOperation.ENTER,
            media_player.Commands.BACK: playdirector.RemoteOperation.BACK,
            media_player.Commands.MENU: playdirector.RemoteOperation.OPTION,
            media_player.Commands.SETTINGS: playdirector.RemoteOperation.PS,
        }
        if cmd in _PS4_BUTTON_MAP and self._device.device_type == "PS4":
            await self._device.send_buttons([_PS4_BUTTON_MAP[cmd]])
            return StatusCodes.OK

        _LOG.warning("[%s] Unhandled command: %s", self.id, cmd_id)
        return StatusCodes.NOT_IMPLEMENTED

    async def browse(
        self, options: media_player.BrowseOptions
    ) -> BrowseResults | StatusCodes:
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
