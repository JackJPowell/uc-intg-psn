"""PlayStation controller UI and bounded UC remote commands.

:copyright: (c) 2026 by Jack Powell.
:license: MPL-2.0, see LICENSE for more details.
"""

import asyncio

from playdirector import BUTTONS, ConsoleError
from ucapi import StatusCodes, media_player, remote
from ucapi.entity import EntityTypes
from ucapi.ui import (
    Buttons,
    EntityCommand,
    Size,
    UiPage,
    create_btn_mapping,
    create_ui_text,
)
from ucapi_framework import create_entity_id
from ucapi_framework.entities import RemoteEntity

SESSION_COMMANDS = {"SESSION_DISCONNECT", "SESSION_RECONNECT"}
SIMPLE_COMMANDS = sorted(BUTTONS | SESSION_COMMANDS | {"HOME"})


def command_parameters(params: dict, sequence: bool = False):
    """Validate the whole sequence before sending any command."""
    commands = params.get("sequence") if sequence else [params.get("command")]
    if not isinstance(commands, list) or not 1 <= len(commands) <= 32:
        raise ValueError("Invalid sequence")
    if any(
        not isinstance(item, str) or item not in SIMPLE_COMMANDS for item in commands
    ):
        raise ValueError("Unknown command")
    if sequence and any(item in SESSION_COMMANDS for item in commands):
        raise ValueError("Session commands cannot be sequenced")
    timings = []
    for key, default, lower, upper in (
        ("repeat", 1, 1, 3),
        ("delay", 0, 0, 1500),
        ("hold", 0, 0, 2500),
    ):
        value = params.get(key, default)
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not lower <= value <= upper
        ):
            raise ValueError("Invalid command timing")
        timings.append(value)
    repeat, delay, hold = timings
    if (
        len(commands) * repeat > 64
        or len(commands) * repeat * (delay + hold + 100) > 60000
    ):
        raise ValueError("Sequence too long")
    return commands, repeat, delay, hold


class PSNRemote(RemoteEntity):
    """Share one library-managed console session across entity commands."""

    def __init__(self, config, device):
        mapping = [
            (Buttons.HOME, "HOME"),
            (Buttons.BACK, "CIRCLE"),
            (Buttons.MENU, "OPTIONS"),
            (Buttons.DPAD_UP, "UP"),
            (Buttons.DPAD_DOWN, "DOWN"),
            (Buttons.DPAD_LEFT, "LEFT"),
            (Buttons.DPAD_RIGHT, "RIGHT"),
            (Buttons.DPAD_MIDDLE, "CROSS"),
        ]
        layout = [
            ("HOME", 0, 0),
            ("PS", 1, 0),
            ("OPTIONS", 2, 0),
            ("SHARE", 3, 0),
            ("UP", 1, 1),
            ("TRIANGLE", 3, 1),
            ("LEFT", 0, 2),
            ("CROSS", 1, 2),
            ("RIGHT", 2, 2),
            ("CIRCLE", 3, 2),
            ("DOWN", 1, 3),
            ("SQUARE", 3, 3),
            ("L1", 0, 4),
            ("L2", 1, 4),
            ("R2", 2, 4),
            ("R1", 3, 4),
            ("L3", 0, 5),
            ("TOUCHPAD", 1, 5),
            ("R3", 3, 5),
        ]
        super().__init__(
            create_entity_id(EntityTypes.REMOTE, config.identifier),
            f"{config.name} Controller",
            features=[
                remote.Features.ON_OFF,
                remote.Features.TOGGLE,
                remote.Features.SEND_CMD,
            ],
            attributes={remote.Attributes.STATE: remote.States.UNKNOWN},
            simple_commands=SIMPLE_COMMANDS,
            button_mapping=[
                create_btn_mapping(Buttons.POWER, EntityCommand("remote.toggle"))
            ]
            + [
                create_btn_mapping(button, remote.create_send_cmd(command))
                for button, command in mapping
            ],
            ui_pages=[
                UiPage(
                    "controller",
                    "PlayStation",
                    grid=Size(4, 6),
                    items=[
                        create_ui_text(name, x, y, cmd=remote.create_send_cmd(name))
                        for name, x, y in layout
                    ],
                )
            ],
        )
        self._device = device
        self._command_lock = asyncio.Lock()
        self.subscribe_to_device(device)

    async def sync_state(self) -> None:
        state = self._device.psn_state
        mapped = (
            remote.States.ON
            if state in {media_player.States.ON, media_player.States.PLAYING}
            else remote.States.OFF
            if state == media_player.States.OFF
            else remote.States.UNAVAILABLE
        )
        self.update({remote.Attributes.STATE: mapped})

    async def command(self, cmd_id: str, params=None, *, websocket=None) -> StatusCodes:
        if not self._device.has_control:
            return StatusCodes.SERVICE_UNAVAILABLE
        try:
            async with self._command_lock:
                power = {
                    remote.Commands.ON: self._device.power_on,
                    remote.Commands.OFF: self._device.power_off,
                    remote.Commands.TOGGLE: self._device.power_toggle,
                }
                if cmd_id in power:
                    await power[cmd_id]()
                elif cmd_id in {
                    remote.Commands.SEND_CMD,
                    remote.Commands.SEND_CMD_SEQUENCE,
                }:
                    commands, repeat, delay, hold = command_parameters(
                        params or {}, cmd_id == remote.Commands.SEND_CMD_SEQUENCE
                    )
                    for _ in range(repeat):
                        for name in commands:
                            if name == "HOME":
                                await self._device.go_home()
                            elif name == "SESSION_DISCONNECT":
                                await self._device.release_session()
                            elif name == "SESSION_RECONNECT":
                                await self._device.reconnect_session()
                            else:
                                await self._device.send_button(
                                    name, hold=hold / 1000 if hold else 0.08
                                )
                            if delay:
                                await asyncio.sleep(delay / 1000)
                else:
                    return StatusCodes.NOT_IMPLEMENTED
            return StatusCodes.OK
        except (ValueError, TypeError):
            return StatusCodes.BAD_REQUEST
        except (ConsoleError, OSError, TimeoutError):
            return StatusCodes.SERVICE_UNAVAILABLE
