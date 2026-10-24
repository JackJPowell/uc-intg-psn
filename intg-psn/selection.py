"""Selectors project coordinator data into UC state.

:copyright: (c) 2026 by Jack Powell.
:license: MPL-2.0, see LICENSE for more details.
"""

from ucapi import StatusCodes, select
from ucapi.entity import EntityTypes
from ucapi_framework import create_entity_id
from ucapi_framework.entities import SelectEntity


class PSNSelect(SelectEntity):
    """Select the saved Remote Play pairing used for controller commands."""

    def __init__(self, config, device):
        self._device = device
        super().__init__(
            create_entity_id(EntityTypes.SELECT, config.identifier, "profile"),
            f"{config.name} Profile",
            attributes={
                select.Attributes.STATE: select.States.UNKNOWN,
                select.Attributes.CURRENT_OPTION: "",
                select.Attributes.OPTIONS: [],
            },
        )
        self.subscribe_to_device(device)

    def _values(self):
        return self._device.profile_options, self._device.selected_profile

    async def sync_state(self) -> None:
        options, current = self._values()
        self.update(
            {
                select.Attributes.STATE: select.States.ON
                if options
                else select.States.UNAVAILABLE,
                select.Attributes.CURRENT_OPTION: current if current in options else "",
                select.Attributes.OPTIONS: options,
            },
            force=True,
        )

    async def command(self, cmd_id: str, params=None, *, websocket=None) -> StatusCodes:
        options, current = self._values()
        if not options:
            return StatusCodes.SERVICE_UNAVAILABLE
        if cmd_id == select.Commands.SELECT_OPTION:
            value = (params or {}).get("option")
            if value not in options:
                return StatusCodes.BAD_REQUEST
        elif cmd_id == select.Commands.SELECT_FIRST:
            value = options[0]
        elif cmd_id == select.Commands.SELECT_LAST:
            value = options[-1]
        elif cmd_id in {select.Commands.SELECT_NEXT, select.Commands.SELECT_PREVIOUS}:
            index = options.index(current) if current in options else 0
            value = options[
                (index + (1 if cmd_id == select.Commands.SELECT_NEXT else -1))
                % len(options)
            ]
        else:
            return StatusCodes.NOT_IMPLEMENTED
        try:
            result = await self._device.select_profile(value)
            return StatusCodes.OK if result else StatusCodes.CONFLICT
        except ValueError:
            return StatusCodes.BAD_REQUEST
        except (RuntimeError, OSError, TimeoutError):
            return StatusCodes.SERVICE_UNAVAILABLE
