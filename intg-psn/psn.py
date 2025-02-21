"""
This module implements the PlayStation Network communication of the Remote Two integration driver.

Uses the [psnawp-ha](https://github.com/--) library with concepts borrowed from the Home Assistant

:copyright: (c) 2023-2024 by Unfolded Circle ApS.
:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import asyncio
import logging
from asyncio import AbstractEventLoop
from enum import IntEnum
from typing import (
    ParamSpec,
    TypeVar,
)

from psnawp_api.psn import PlaystationNetwork, PlaystationNetworkData
from psnawp_api.core.psnawp_exceptions import PSNAWPAuthenticationError
from config import PSNDevice

from pyee.asyncio import AsyncIOEventEmitter

_LOG = logging.getLogger(__name__)

BACKOFF_MAX = 30
BACKOFF_SEC = 2
ARTWORK_WIDTH = 400
ARTWORK_HEIGHT = 400


class EVENTS(IntEnum):
    """Internal driver events."""

    CONNECTING = 0
    CONNECTED = 1
    DISCONNECTED = 2
    ERROR = 4
    UPDATE = 5


_PSNAccountT = TypeVar("_PSNAccountT", bound="PSNAccount")
_P = ParamSpec("_P")


class PSNAccount:
    """Representing a PSN Account."""

    def __init__(
        self,
        device: PSNDevice,
        loop: AbstractEventLoop | None = None,
    ) -> None:
        """Create instance."""
        self._loop: AbstractEventLoop = loop or asyncio.get_running_loop()
        self.events = AsyncIOEventEmitter(self._loop)
        self._is_on: bool = False
        self._psn: PlaystationNetwork | None = None
        self._psn_data: PlaystationNetworkData | None = None
        self._device: PSNDevice = device
        self._connection_attempts: int = 0
        self._polling = None
        self._poll_interval: int = 30
        self._state: str | None = None

    @property
    def identifier(self) -> str:
        """Return the device identifier."""
        if not self._device.identifier:
            raise ValueError("Instance not initialized, no identifier available")
        return self._device.identifier

    @property
    def log_id(self) -> str:
        """Return a log identifier."""
        return self._device.name if self._device.name else self._device.identifier

    @property
    def name(self) -> str:
        """Return the device name."""
        return self._device.name

    @property
    def is_on(self) -> bool | None:
        """Whether the Apple TV is on or off. Returns None if not connected."""
        if self._psn and self._psn_data.available is True:
            self._is_on = True
        return self._is_on

    @property
    def state(self) -> str | None:
        """Return the device state."""
        return self._state

    def _backoff(self) -> float:
        if self._connection_attempts * BACKOFF_SEC >= BACKOFF_MAX:
            return BACKOFF_MAX

        return self._connection_attempts * BACKOFF_SEC

    def _handle_disconnect(self):
        """Handle that the device disconnected and restart connect loop."""
        _ = asyncio.ensure_future(self._stop_polling())
        if self._psn:
            self._psn = None
        self.events.emit(EVENTS.DISCONNECTED, self._device.identifier)

    async def connect(self) -> None:
        """Establish connection to PSN."""
        if self._is_on is True:
            return
        self._is_on = True

        self.events.emit(EVENTS.CONNECTING, self._device.identifier)

        try:
            self._psn = PlaystationNetwork(self._device.npsso)
            self._psn_data = self._psn.get_data()
        except PSNAWPAuthenticationError as ex:
            _LOG.error(
                "Your NPSSO Token has expired. Please rerun setup to update. %s", ex
            )
            self.events.emit(EVENTS.ERROR, self._device.identifier)
            return

        self.events.emit(EVENTS.CONNECTED, self._device.identifier)
        _LOG.debug("[%s] Connected", self.log_id)
        self.update_attributes()
        await self._start_polling()

    async def disconnect(self) -> None:
        """Disconnect from PSN."""
        _LOG.debug("[%s] Disconnecting from device", self.log_id)
        self._is_on = False
        await self._stop_polling()

        try:
            if self._psn:
                self._psn = None
        except Exception as err:  # pylint: disable=broad-exception-caught
            _LOG.exception(
                "[%s] An error occurred while disconnecting: %s", self.log_id, err
            )
        finally:
            self._psn = None

    async def _start_polling(self) -> None:
        if self._psn is None:
            _LOG.warning("[%s] Polling not started, PSN object is None", self.log_id)
            self.events.emit(EVENTS.ERROR, "Polling not started, PSN object is None")
            return

        self._polling = self._loop.create_task(self._poll_worker())
        _LOG.debug("[%s] Polling started", self.log_id)

    async def _stop_polling(self) -> None:
        if self._polling:
            self._polling.cancel()
            self._polling = None
            _LOG.debug("[%s] Polling stopped", self.log_id)
        else:
            _LOG.debug("[%s] Polling was already stopped", self.log_id)

    def update_attributes(self) -> None:
        _LOG.debug("[%s] Process update", self.log_id)

        update = {}
        if not self._psn:
            self._psn = PlaystationNetwork(self._device.npsso)
        self._psn_data = self._psn.get_data()

        update["state"] = "OFF"
        if (
            self._psn_data.platform.get("platform", "")
            and self._psn_data.platform.get("onlineStatus", "") == "online"
        ):
            update["state"] = "ON"
            if (
                self._psn_data.available
                and self._psn_data.title_metadata.get("npTitleId") is not None
            ):
                update["state"] = "PLAYING"

        if self._psn_data.title_metadata.get("npTitleId"):
            update["title"] = self._psn_data.title_metadata.get("titleName")
            update["artist"] = self._psn_data.title_metadata.get("format")

        if self._psn_data.title_metadata.get("npTitleId"):
            title = self._psn_data.title_metadata
            if title.get("format", "") == "PS5":
                update["artwork"] = title.get("conceptIconUrl")
            if title.get("format", "") == "PS4":
                update["artwork"] = title.get("npTitleIconUrl")

        self.events.emit(EVENTS.UPDATE, self._device.identifier, update)

    async def _poll_worker(self) -> None:
        await asyncio.sleep(2)
        while self._psn is not None:
            self.update_attributes()
            await asyncio.sleep(self._poll_interval)
