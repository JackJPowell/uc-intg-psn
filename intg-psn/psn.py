"""
This module implements the PlayStation Network communication of the Remote integration driver.

Uses the [psnawp-ha](https://github.com/--) library with concepts borrowed from the Home Assistant

:copyright: (c) 2023-2024
:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import asyncio
import logging
import os
import sys
from asyncio import AbstractEventLoop
from dataclasses import dataclass
from typing import Any

# Add parent directory to path for ucapi_base module (before it's published)
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PARENT_DIR = os.path.dirname(_SCRIPT_DIR)
if _PARENT_DIR not in sys.path:
    sys.path.insert(0, _PARENT_DIR)

from config import PSNDevice  # noqa: E402
from psnawp_api import PSNAWP  # noqa: E402
from psnawp_api.core.psnawp_exceptions import PSNAWPAuthenticationError  # noqa: E402
from psnawp_api.models.user import User  # noqa: E402
from pyrate_limiter import Duration, Rate  # noqa: E402
from ucapi_base.device import PollingDevice, DeviceEvents  # noqa: E402

_LOG = logging.getLogger(__name__)

ARTWORK_WIDTH = 400
ARTWORK_HEIGHT = 400

# Map ucapi_base DeviceEvents to EVENTS for backwards compatibility
EVENTS = DeviceEvents


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

    def close(self):
        """Close the PSN connection and cleanup resources."""
        try:
            if hasattr(self.psn, "authenticator") and hasattr(
                self.psn.authenticator, "session"
            ):
                # Close the aiohttp session if it exists
                session = self.psn.authenticator.session
                if session and not session.closed:
                    # Schedule the session close in the event loop
                    try:
                        loop = asyncio.get_event_loop()
                        if loop.is_running():
                            asyncio.create_task(session.close())
                        else:
                            loop.run_until_complete(session.close())
                    except Exception as ex:  # pylint: disable=broad-exception-caught
                        _LOG.debug("Error closing session: %s", ex)
        except Exception as ex:  # pylint: disable=broad-exception-caught
            _LOG.debug("Error during PSN cleanup: %s", ex)

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


class PSNAccount(PollingDevice):
    """Representing a PSN Account using PollingDevice base class."""

    def __init__(
        self,
        device: PSNDevice,
        loop: AbstractEventLoop | None = None,
    ) -> None:
        """Create instance with 45 second poll interval."""
        super().__init__(device, loop, poll_interval=45)
        self._psn: PlaystationNetwork | None = None
        self._psn_data: PlaystationNetworkData | None = None

    @property
    def identifier(self) -> str:
        """Return the device identifier."""
        if not self._device_config.identifier:
            raise ValueError("Instance not initialized, no identifier available")
        return self._device_config.identifier

    @property
    def name(self) -> str:
        """Return the device name."""
        return self._device_config.name

    @property
    def address(self) -> str | None:
        """Return the device address (PSN doesn't have a physical address)."""
        return self._device_config.identifier

    @property
    def is_on(self) -> bool:
        """Whether the PSN is on or off."""
        if self._psn and self._psn_data and self._psn_data.available is True:
            return True
        return False

    async def _establish_connection(self) -> None:
        """Establish connection to PSN - called by base class connect()."""
        try:
            self._psn = PlaystationNetwork(self._device_config.npsso)
            _LOG.debug("[%s] PSN connection established", self.log_id)
            # Do initial attribute update
            await self._poll_device()
        except PSNAWPAuthenticationError as ex:
            _LOG.error(
                "[%s] NPSSO Token has expired. Please rerun setup to update. %s",
                self.log_id,
                ex,
            )
            raise
        except Exception as ex:  # pylint: disable=broad-exception-caught
            _LOG.error("[%s] Error connecting to PSN: %s", self.log_id, ex)
            raise

    async def disconnect(self) -> None:
        """Disconnect from PSN."""
        _LOG.debug("[%s] Disconnecting from device", self.log_id)

        # Call base class disconnect to stop polling
        await super().disconnect()

        # Clean up PSN connection
        if self._psn:
            try:
                self._psn.close()
            except Exception as err:  # pylint: disable=broad-exception-caught
                _LOG.exception(
                    "[%s] An error occurred while disconnecting: %s", self.log_id, err
                )
            finally:
                self._psn = None

    async def _poll_device(self) -> None:
        """
        Poll the device for status updates - called by base class.

        This method is called periodically by the PollingDevice base class.
        """
        _LOG.debug("[%s] Polling PSN for updates", self.log_id)

        if not self._psn:
            _LOG.warning("[%s] PSN object is None, cannot poll", self.log_id)
            return

        try:
            self._psn_data = await self._loop.run_in_executor(None, self._psn.get_data)

            if not self._psn_data:
                _LOG.warning(
                    "[%s] PSN data is None, cannot update attributes", self.log_id
                )
                return

            # Build update dictionary
            update = {"state": "OFF"}

            if (
                self._psn_data.platform
                and self._psn_data.platform.get("platform", "")
                and self._psn_data.platform.get("onlineStatus", "") == "online"
            ):
                update["state"] = "ON"
                if (
                    self._psn_data.available
                    and self._psn_data.title_metadata
                    and self._psn_data.title_metadata.get("npTitleId") is not None
                ):
                    update["state"] = "PLAYING"

            self._state = update["state"]

            # Add title metadata if available
            if self._psn_data.title_metadata and self._psn_data.title_metadata.get(
                "npTitleId"
            ):
                update["title"] = self._psn_data.title_metadata.get("titleName")
                update["artist"] = self._psn_data.title_metadata.get("format")

                title = self._psn_data.title_metadata
                if title.get("format", "") == "PS5":
                    update["artwork"] = title.get("conceptIconUrl")
                elif title.get("format", "") == "PS4":
                    update["artwork"] = title.get("npTitleIconUrl")

            # Emit update event
            self.events.emit(EVENTS.UPDATE, self.identifier, update)
            _LOG.debug("[%s] PSN update emitted: %s", self.log_id, update.get("state"))

        except Exception as ex:  # pylint: disable=broad-exception-caught
            _LOG.error("[%s] Error while polling PSN: %s", self.log_id, ex)
            self.events.emit(
                EVENTS.ERROR,
                self.identifier,
                f"Error while polling PSN: {ex}",
            )
