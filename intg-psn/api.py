"""
API Wrapper for PlayStation Network using PSNAWP.

:copyright: (c) 2025 by Jack Powell.
:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from psnawp_api import PSNAWP  # noqa: E402
from psnawp_api.models.user import User  # noqa: E402
from pyrate_limiter import Duration, Rate  # noqa: E402
from requests import Session

_LOG = logging.getLogger(__name__)


@dataclass
class PlayStationNetworkData:
    """Dataclass representing data retrieved from the PlayStation Network api."""

    presence: dict[str, Any]
    username: str
    account_id: str
    available: bool
    title_metadata: dict[str, Any]
    platform: dict[str, Any]
    registered_platforms: list[str]


class PlayStationNetwork:
    """Helper Class to return PlayStation Network data in an easy to use structure.

    :raises PSNAWPAuthenticationError: If npsso code is expired or is incorrect.
    """

    def __init__(self, npsso: str, loop: asyncio.AbstractEventLoop) -> None:
        """Initialize PlayStationNetwork with NPSSO token."""
        self.rate = Rate(300, Duration.MINUTE * 15)
        self.psn = PSNAWP(npsso, rate_limit=self.rate)
        self.client = self.psn.me()
        self.user: User | None = None
        self.data: PlayStationNetworkData | None = None
        self.loop = loop

    def validate_connection(self):
        """Validate the PSN connection by fetching the current user."""
        self.psn.me()

    def get_user(self):
        """Get the PSN user object."""
        self.user = self.psn.user(online_id="me")
        return self.user

    def close(self):
        """Close the PSN connection and cleanup resources."""
        try:
            if hasattr(self.psn, "authenticator") and hasattr(
                self.psn.authenticator, "request_builder"
            ):
                # Close the requests.Session to release connection pool resources
                session: Session = self.psn.authenticator.request_builder.session
                if session:
                    session.close()
                    _LOG.debug("PSN session closed successfully")
        except Exception as ex:  # pylint: disable=broad-exception-caught
            _LOG.debug("Error during PSN cleanup: %s", ex)

    def get_data(self):
        """Get the PlayStation Network data."""
        data: PlayStationNetworkData = PlayStationNetworkData(
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
