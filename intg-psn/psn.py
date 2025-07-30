"""
This module implements the PlayStation Network communication of the Remote Two integration driver.

Uses the [psnawp-ha](https://github.com/--) library with concepts borrowed from the Home Assistant

:copyright: (c) 2023-2024 by Unfolded Circle ApS.
:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any
from asyncio import AbstractEventLoop
from enum import IntEnum
import json
from typing import (
    ParamSpec,
    TypeVar,
)

from config import PSNDevice
from psnawp_api.core.psnawp_exceptions import PSNAWPAuthenticationError
from psnawp_api import PSNAWP
from psnawp_api.models.user import User
from pyee.asyncio import AsyncIOEventEmitter
from pyrate_limiter import Duration, Rate

_LOG = logging.getLogger(__name__)

BACKOFF_MAX = 30
BACKOFF_SEC = 2
ARTWORK_WIDTH = 400
ARTWORK_HEIGHT = 400
WEBSOCKET_WATCHDOG_INTERVAL = 10
CONNECTION_RETRIES = 10


class EVENTS(IntEnum):
    """Internal driver events."""

    CONNECTING = 0
    CONNECTED = 1
    DISCONNECTED = 2
    ERROR = 4
    UPDATE = 5


_PSNAccountT = TypeVar("_PSNAccountT", bound="PSNAccount")
_P = ParamSpec("_P")


@dataclass
class PlaystationNetworkData:
    """Dataclass representing data retrieved from the Playstation Network api."""

    presence: dict[str, Any]
    username: str
    account_id: str
    available: bool
    title_metadata: dict[str, Any]
    platform: dict[str, Any]
    registered_platforms: list[str]


class PlaystationNetwork:
    """Helper Class to return playstation network data in an easy to use structure

    :raises PSNAWPAuthenticationError: If npsso code is expired or is incorrect."""

    def __init__(self, npsso: str):
        self.rate = Rate(300, Duration.MINUTE * 15)
        self.psn = PSNAWP(npsso, rate_limit=self.rate)
        self.client = self.psn.me()
        self.user: User | None = None
        self.data: PlaystationNetworkData | None = None

    def validate_connection(self):
        self.psn.me()

    def get_user(self):
        self.user = self.psn.user(online_id="me")
        return self.user

    def get_data(self):
        data: PlaystationNetworkData = PlaystationNetworkData(
            {}, "", "", False, {}, {}, []
        )

        if not self.user:
            self.user = self.get_user()

        devices = self.client.get_account_devices()
        for device in devices:
            if (
                device.get("deviceType") in ["PS5", "PS4"]
                and device.get("deviceType") not in data.registered_platforms
            ):
                data.registered_platforms.append(device.get("deviceType", ""))

        data.username = self.user.online_id
        data.account_id = self.user.account_id
        data.presence = self.user.get_presence()

        data.available = (
            data.presence.get("basicPresence", {}).get("availability")
            == "availableToPlay"
        )
        data.platform = data.presence.get("basicPresence", {}).get(
            "primaryPlatformInfo"
        )
        game_title_info_list = data.presence.get("basicPresence", {}).get(
            "gameTitleInfoList"
        )

        if game_title_info_list:
            data.title_metadata = game_title_info_list[0]

        self.data = data
        return self.data


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
        self._poll_interval: int = 45
        self._state: str | None = "OFF"
        self._reconnect_retry: int = 0

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
        """Whether the PSN is on or off. Returns None if not connected."""
        if self._psn and self._psn_data.available is True:
            self._is_on = True
        return self._is_on

    @property
    def state(self) -> str | None:
        """Return the device state."""
        return self._state

    def _handle_disconnect(self):
        """Handle that the device disconnected and restart connect loop."""
        _ = asyncio.ensure_future(self._stop_polling())
        if self._psn:
            self._psn = None
        self.events.emit(EVENTS.DISCONNECTED, self._device.identifier)

    async def connect(self) -> None:
        """Establish connection to PSN."""
        if self._is_on is True:
            _LOG.info("PSN is_on. Skipping reconnect")
            return

        self.events.emit(EVENTS.CONNECTING, self._device.identifier)

        try:
            self._psn = PlaystationNetwork(self._device.npsso)
            self.events.emit(EVENTS.CONNECTED, self._device.identifier)
            _LOG.debug("[%s] Connected", self.log_id)
            self.update_attributes()
            self._is_on = True
            await self._start_polling()
        except PSNAWPAuthenticationError as ex:
            _LOG.error(
                "Your NPSSO Token has expired. Please rerun setup to update. %s", ex
            )
            self.events.emit(
                EVENTS.ERROR,
                self._device.identifier,
                "Your NPSSO Token has expired. Please rerun setup to update.",
            )
            self._is_on = False
            return
        except Exception as ex:  # pylint: disable=broad-exception-caught
            _LOG.error("An error occured when trying to connect to the PSN:. %s", ex)
            self.events.emit(
                EVENTS.ERROR,
                self._device.identifier,
                "An error occured when trying to connect to the PSN",
            )
            self._is_on = False
            return

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
        if self._polling:
            _LOG.debug("[%s] Polling is already running", self.log_id)
            return

        if self._psn is None:
            _LOG.warning("[%s] Polling not started, PSN object is None", self.log_id)
            return

        self._polling = self._loop.create_task(self._poll_worker())
        _LOG.debug("[%s] Polling started", self.log_id)

    async def _stop_polling(self) -> None:
        if self._polling:
            try:
                self._polling.cancel()
                await self._polling
            except asyncio.CancelledError:
                _LOG.debug("[%s] Polling task was cancelled", self.log_id)
            finally:
                self._polling = None
            _LOG.debug("[%s] Polling stopped", self.log_id)
        else:
            _LOG.debug("[%s] Polling was already stopped", self.log_id)

    def update_attributes(self) -> None:
        """Update media player attributes and forward ws message."""
        _LOG.debug("[%s] Process update", self.log_id)

        update = {}
        try:
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

            self._state = update["state"]
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
        except Exception as ex:  # pylint: disable=broad-exception-caught
            _LOG.error("Error while updating data from PSN: %s", ex)
            self.events.emit(
                EVENTS.ERROR,
                self._device.identifier,
                "Error while updating data from PSN",
            )

    async def _poll_worker(self) -> None:
        try:
            await asyncio.sleep(2)
            while self._psn is not None:
                try:
                    self.update_attributes()
                    _LOG.debug("PSN Request made to update attributes")
                except Exception as ex:  # pylint: disable=broad-exception-caught
                    _LOG.error(
                        "[%s] Error while updating attributes: %s", self.log_id, ex
                    )
                    _LOG.warning(
                        "[%s] Attempting to reconnect after error", self.log_id
                    )
                    await self.connect()  # Attempt to reconnect after an error
                await asyncio.sleep(self._poll_interval)
            _LOG.warning(
                "[%s] PSN object is None, attempting to reconnect", self.log_id
            )
            await asyncio.sleep(self._poll_interval)
            self._is_on = False
            await self.connect()
        except asyncio.CancelledError:
            _LOG.debug("[%s] Polling task was cancelled", self.log_id)
        except Exception as ex:  # pylint: disable=broad-exception-caught
            _LOG.error("[%s] Error in polling task: %s", self.log_id, ex)
        finally:
            _LOG.debug("[%s] Polling task exited", self.log_id)
