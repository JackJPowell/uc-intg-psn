"""PlayStation game browser and console commands.

:copyright: (c) 2026 by Jack Powell.
:license: MPL-2.0, see LICENSE for more details.
"""

import logging

from cloud import CloudError, ConsoleSelectionError, normalize_title_id
from playdirector import BUTTONS, ConsoleError
from ucapi import StatusCodes, media_player
from ucapi.api_definitions import Pagination
from ucapi.entity import EntityTypes
from ucapi.media_player import BrowseMediaItem, BrowseResults
from ucapi_framework import create_entity_id
from ucapi_framework.entities import MediaPlayerEntity

_LOG = logging.getLogger(__name__)
BUTTON_MAP = {
    media_player.Commands.CURSOR_UP: "UP",
    media_player.Commands.CURSOR_DOWN: "DOWN",
    media_player.Commands.CURSOR_LEFT: "LEFT",
    media_player.Commands.CURSOR_RIGHT: "RIGHT",
    media_player.Commands.CURSOR_ENTER: "CROSS",
    media_player.Commands.BACK: "CIRCLE",
    media_player.Commands.MENU: "OPTIONS",
    media_player.Commands.SETTINGS: "PS",
}


class PSNMediaPlayer(MediaPlayerEntity):
    """Project coordinator state; browse and launch through its shared services."""

    def __init__(self, config, device):
        features = [media_player.Features.BROWSE_MEDIA] if device.has_psn else []
        if device.can_launch_games:
            features.append(media_player.Features.PLAY_MEDIA)
        if device.has_control:
            features.extend(
                [
                    media_player.Features.ON_OFF,
                    media_player.Features.TOGGLE,
                    media_player.Features.DPAD,
                    media_player.Features.HOME,
                    media_player.Features.MENU,
                    media_player.Features.SETTINGS,
                ]
            )
        super().__init__(
            create_entity_id(EntityTypes.MEDIA_PLAYER, config.identifier),
            config.name,
            features=features,
            attributes={
                media_player.Attributes.STATE: media_player.States.UNKNOWN,
                media_player.Attributes.MEDIA_TITLE: "",
                media_player.Attributes.MEDIA_ARTIST: "",
                media_player.Attributes.MEDIA_IMAGE_URL: "",
            },
            device_class=media_player.DeviceClasses.STREAMING_BOX,
            options={media_player.Options.SIMPLE_COMMANDS: sorted(BUTTONS)}
            if device.has_control
            else {},
        )
        self._device = device
        self.subscribe_to_device(device)

    async def sync_state(self) -> None:
        self.update(
            {
                media_player.Attributes.STATE: self._device.psn_state,
                media_player.Attributes.MEDIA_TITLE: self._device.psn_media_title,
                media_player.Attributes.MEDIA_ARTIST: self._device.psn_media_artist,
                media_player.Attributes.MEDIA_IMAGE_URL: self._device.psn_media_image_url,
            }
        )

    async def command(self, cmd_id: str, params=None, *, websocket=None) -> StatusCodes:
        params = params or {}
        try:
            if cmd_id == media_player.Commands.PLAY_MEDIA:
                if not self._device.can_launch_games:
                    return StatusCodes.NOT_IMPLEMENTED
                await self._device.launch_game(
                    normalize_title_id(params.get("media_id", ""))
                )
            else:
                if not self._device.has_control:
                    return StatusCodes.SERVICE_UNAVAILABLE
                actions = {
                    media_player.Commands.ON: self._device.power_on,
                    media_player.Commands.OFF: self._device.power_off,
                    media_player.Commands.TOGGLE: self._device.power_toggle,
                    media_player.Commands.HOME: self._device.go_home,
                }
                if cmd_id in actions:
                    await actions[cmd_id]()
                else:
                    button = (
                        BUTTON_MAP.get(cmd_id)
                        or params.get("command")
                        or params.get("simple_command")
                        or (cmd_id if cmd_id in BUTTONS else None)
                    )
                    if button not in BUTTONS:
                        return StatusCodes.NOT_IMPLEMENTED
                    await self._device.send_button(button)
            return StatusCodes.OK
        except ConsoleSelectionError:
            return StatusCodes.CONFLICT
        except (TypeError, ValueError):
            return StatusCodes.BAD_REQUEST
        except (ConsoleError, CloudError, OSError, TimeoutError):
            return StatusCodes.SERVICE_UNAVAILABLE
        except Exception:  # noqa: BLE001 - entity command boundary reports a status code
            _LOG.exception("PlayStation command failed")
            return StatusCodes.SERVER_ERROR

    async def browse(self, options: media_player.BrowseOptions):
        if not self._device.has_psn or not self._device.psn_authenticated:
            return StatusCodes.SERVICE_UNAVAILABLE
        paging = options.paging
        folder = options.media_id or "psn_library"
        try:
            if folder == "psn_library":
                views = ["psn_games"]
                if self._device.can_launch_games:
                    views.append("installed_games")
                labels = {
                    "psn_games": "Recently played",
                    "installed_games": "Installed games & apps",
                }
                all_items = [
                    BrowseMediaItem(
                        media_id=key,
                        title=labels[key],
                        media_class=media_player.MediaClass.DIRECTORY,
                        can_browse=True,
                        can_play=False,
                    )
                    for key in views
                ]
                items, total, title = (
                    all_items[paging.offset : paging.offset + paging.limit],
                    len(all_items),
                    "PlayStation library",
                )
            elif folder not in {"installed_games", "psn_games"}:
                title_id = normalize_title_id(folder.removeprefix("details:"))
                stats = await self._device.get_game_details(title_id)
                title = stats.name if stats and stats.name else title_id
                duration = stats.play_duration if stats else None
                details = [
                    (
                        "Playtime",
                        f"{duration.total_seconds() / 3600:.1f} hours"
                        if duration is not None
                        else "Unavailable",
                    ),
                    (
                        "Play count",
                        str(stats.play_count)
                        if stats and stats.play_count is not None
                        else "Unavailable",
                    ),
                    (
                        "First played",
                        stats.first_played_date_time.isoformat()
                        if stats and stats.first_played_date_time
                        else "Unavailable",
                    ),
                    (
                        "Last played",
                        stats.last_played_date_time.isoformat()
                        if stats and stats.last_played_date_time
                        else "Unavailable",
                    ),
                ]
                all_items = [
                    BrowseMediaItem(
                        media_id=title_id,
                        title="Launch game",
                        can_play=self._device.can_launch_games,
                        can_browse=False,
                        media_class=media_player.MediaClass.GAME,
                    )
                ] + [
                    BrowseMediaItem(
                        media_id=f"stat:{index}:{title_id}",
                        title=f"{key}: {value}",
                        can_play=False,
                        can_browse=False,
                    )
                    for index, (key, value) in enumerate(details)
                ]
                items, total = (
                    all_items[paging.offset : paging.offset + paging.limit],
                    len(all_items),
                )
            elif folder in {"installed_games", "psn_games"}:
                if folder == "installed_games":
                    games, total = await self._device.get_installed_game_library(
                        paging.limit, paging.offset
                    )
                    title = "Installed games & apps"
                else:
                    rows, total = await self._device.get_game_library(
                        paging.limit, paging.offset
                    )
                    games = [
                        {
                            "title_id": row.title_id,
                            "name": row.name,
                            "image": row.image_url,
                        }
                        for row in rows
                        if row.title_id
                    ]
                    title = "Recently played"
                items = [
                    BrowseMediaItem(
                        media_id=normalize_title_id(game["title_id"]),
                        title=(game.get("name") or game["title_id"])[:255],
                        thumbnail=str(game.get("image") or "icon://uc:gamepad-modern"),
                        media_class=media_player.MediaClass.GAME,
                        can_browse=True,
                        can_play=self._device.can_launch_games,
                    )
                    for game in games
                ]
            else:
                return StatusCodes.BAD_REQUEST
            return BrowseResults(
                media=BrowseMediaItem(
                    media_id=folder,
                    title=title[:255],
                    media_class=media_player.MediaClass.DIRECTORY,
                    can_browse=True,
                    can_play=False,
                    items=items,
                ),
                pagination=Pagination(
                    page=paging.page, limit=paging.limit, count=total
                ),
            )
        except (ValueError, TypeError):
            return StatusCodes.BAD_REQUEST
        except (CloudError, OSError, TimeoutError):
            return StatusCodes.SERVICE_UNAVAILABLE
