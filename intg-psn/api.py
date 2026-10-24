"""
API Wrapper for PlayStation Network using PSNAWP.

:copyright: (c) 2025 by Jack Powell.
:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

from psnawp_api import PSNAWP
from psnawp_api.core.psnawp_exceptions import (
    PSNAWPNotFoundError,
    PSNAWPTooManyRequestsError,
)
from psnawp_api.models.user import User
from pyrate_limiter import Duration, Limiter, Rate
from requests import Session
from requests.adapters import HTTPAdapter

_LOG = logging.getLogger(__name__)


class PSNRequestLimiter(Limiter):
    """Share a conservative process-wide budget without blocking worker shutdown."""

    def try_acquire(self, name="psnawp-limiter", weight=1, **kwargs):
        if not super().try_acquire(name, weight, blocking=False):
            raise PSNAWPTooManyRequestsError(
                "Local PSN request budget exhausted; retry later"
            )
        return True


# Includes setup, authentication, cloud commands and every configured device.
# Separate integration processes and other PSN apps have independent budgets.
_PSN_REQUEST_LIMITER = PSNRequestLimiter(Rate(300, Duration.MINUTE * 15))


class TimeoutAdapter(HTTPAdapter):
    """Bound requests and share quota without replacing PSNAWP's own limiter."""

    def send(self, request, **kwargs):
        _PSN_REQUEST_LIMITER.try_acquire()
        if kwargs.get("timeout") is None:
            kwargs["timeout"] = (3, 10)
        return super().send(request, **kwargs)


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
        self.psn.authenticator.request_builder.session.mount(
            "https://", TimeoutAdapter()
        )
        self.client = self.psn.me()
        self.user: User | None = None
        self.data: PlayStationNetworkData | None = None
        self.loop = loop
        self._pages: dict[tuple[int, int], tuple[float, list, int]] = {}
        self._titles: dict[str, Any] = {}
        self._title_expiry: dict[str, float] = {}
        self._platforms: list[str] = []
        self._platform_expiry = 0.0
        self._metadata: dict[str, tuple[float, dict]] = {}

    def validate_connection(self):
        """Validate the PSN connection by fetching the current user."""
        self.get_user()

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
        except Exception as ex:  # noqa: BLE001 - cleanup must tolerate upstream failures
            _LOG.debug("Error during PSN cleanup: %s", ex)

    def get_title_stats(self, limit: int = 10, offset: int = 0) -> list:
        """
        Fetch a page of the user's played game library.

        :param limit: Maximum number of titles to return (maps to paging limit).
        :param offset: 0-based start offset.
        :return: List of TitleStats objects.
        """
        return self.get_game_page(limit, offset)[0]

    def get_game_page(self, limit: int = 10, offset: int = 0) -> tuple[list, int]:
        """Share bounded, cached PSNAWP pages between browser and metadata."""
        limit, offset = max(1, min(100, int(limit))), max(0, int(offset))
        key = (limit, offset)
        cached = self._pages.get(key)
        if cached and cached[0] > time.monotonic():
            return cached[1], cached[2]
        iterator = self.client.title_stats(limit=limit, offset=offset, page_size=limit)
        titles = list(iterator)
        total = len(iterator)
        self._pages[key] = (time.monotonic() + 300, titles, total)
        self._titles.update(
            {
                item.title_id.removesuffix("_00"): item
                for item in titles
                if item.title_id
            }
        )
        self._title_expiry.update(
            {
                item.title_id.removesuffix("_00"): time.monotonic() + 300
                for item in titles
                if item.title_id
            }
        )
        return titles, total

    def get_game_details(self, title_id: str):
        """Find stats without repeatedly rescanning the same account history."""
        from cloud import normalize_title_id

        title_id = normalize_title_id(title_id)
        deadline = time.monotonic() + 15
        # Refresh a cached record through its originating page when necessary.
        for offset in range(0, 1600, 100):
            if (
                title_id in self._titles
                and self._title_expiry.get(title_id, 0) > time.monotonic()
            ):
                return self._titles[title_id]
            if time.monotonic() >= deadline:
                break
            rows, total = self.get_game_page(100, offset)
            if offset + len(rows) >= total:
                break
        return (
            self._titles.get(title_id)
            if self._title_expiry.get(title_id, 0) > time.monotonic()
            else None
        )

    def get_trophy_summary(self) -> dict[str, int]:
        """Retrieve account totals through the public PSNAWP model."""
        summary = self.client.trophy_summary()
        trophies = summary.earned_trophies
        return {
            "level": summary.trophy_level,
            "platinum": trophies.platinum,
            "earned": sum(
                getattr(trophies, kind)
                for kind in ("bronze", "silver", "gold", "platinum")
            ),
        }

    def get_title_progress(self, title_id: str) -> int | None:
        """Read the active game's trophy percentage without a parallel client."""
        from cloud import normalize_title_id

        title_id = normalize_title_id(title_id)
        rows = self.client.trophy_titles_for_title([title_id + "_00"])
        return next((row.progress for row in rows if row.progress is not None), None)

    def get_title_metadata(self, title_ids: list[str]) -> dict[str, dict]:
        """Enrich inventory with cached history and public trophy metadata."""
        now = time.monotonic()
        details = {
            title: self._metadata[title][1]
            for title in title_ids
            if title in self._metadata and self._metadata[title][0] > now
        }
        title_ids = [title for title in title_ids if title not in details]
        if not title_ids:
            return details
        deadline = now + 20
        # Share history pages; never reimplement Sony's game-list paginator.
        for offset in range(0, 800, 100):
            if time.monotonic() >= deadline:
                break
            _, total = self.get_game_page(100, offset)
            if set(title_ids).issubset(self._titles) or offset + 100 >= total:
                break
        missing = []
        for title_id in title_ids:
            row = self._titles.get(title_id)
            if row:
                details[title_id] = {
                    "name": row.name or title_id,
                    "image": str(row.image_url or ""),
                }
            else:
                missing.append(title_id)
        for offset in range(0, len(missing), 20):
            if time.monotonic() >= deadline:
                break
            try:
                rows = self.client.trophy_titles_for_title(
                    [title + "_00" for title in missing[offset : offset + 20]]
                )
                for row in rows:
                    if row.np_title_id:
                        details[row.np_title_id.removesuffix("_00")] = {
                            "name": row.title_name or "",
                            "image": row.title_icon_url or "",
                        }
            except PSNAWPNotFoundError:
                continue
        for title in title_ids:
            metadata = details.setdefault(title, {"name": title, "image": ""})
            self._metadata[title] = (time.monotonic() + 3600, metadata)
        return details

    def get_data(self):
        """Get the PlayStation Network data."""
        data: PlayStationNetworkData = PlayStationNetworkData(
            {}, "", "", False, {}, {}, []
        )

        if not self.user:
            self.user = self.get_user()

        if time.monotonic() >= self._platform_expiry:
            devices = self.client.get_account_devices()
            self._platforms = sorted(
                {
                    row["deviceType"]
                    for row in devices
                    if row.get("deviceType") in {"PS4", "PS5"}
                }
            )
            self._platform_expiry = time.monotonic() + 600
        data.registered_platforms = self._platforms

        data.username = self.user.online_id
        data.account_id = self.user.account_id
        data.presence = self.user.get_presence()

        data.available = (
            data.presence.get("basicPresence", {}).get("availability")
            == "availableToPlay"
        )
        data.platform = (
            data.presence.get("basicPresence", {}).get("primaryPlatformInfo") or {}
        )
        game_title_info_list = data.presence.get("basicPresence", {}).get(
            "gameTitleInfoList"
        )

        if game_title_info_list:
            data.title_metadata = game_title_info_list[0]

        self.data = data
        return self.data
