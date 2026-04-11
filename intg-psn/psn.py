"""
This module implements the PlayStation Network communication of the Remote integration driver.

:copyright: (c) 2025 by Jack Powell.
:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import logging
from asyncio import AbstractEventLoop
from itertools import islice

from api import PlayStationNetwork, PlayStationNetworkData
from const import PSNConfig
from psnawp_api.core.psnawp_exceptions import PSNAWPAuthenticationError
from ucapi import media_player
from ucapi_framework import BaseIntegrationDriver
from ucapi_framework.device import DeviceEvents, PollingDevice

_LOG = logging.getLogger(__name__)

ARTWORK_WIDTH = 400
ARTWORK_HEIGHT = 400


class PSNAccount(PollingDevice):
    """Representing a PSN Account using PollingDevice base class."""

    def __init__(
        self,
        device_config: PSNConfig,
        loop: AbstractEventLoop | None = None,
        config_manager=None,
        driver: BaseIntegrationDriver | None = None,
    ) -> None:
        """Create instance with 45 second poll interval."""
        super().__init__(
            device_config,
            loop,
            poll_interval=45,
            config_manager=config_manager,
            driver=driver,
        )
        self._psn: PlayStationNetwork | None = None
        self._psn_data: PlayStationNetworkData | None = None

        # Device state — read by entities via sync_state()
        self.psn_state: media_player.States = media_player.States.UNKNOWN
        self.psn_media_title: str = ""
        self.psn_media_artist: str = ""
        self.psn_media_image_url: str = ""
        self._total_game_count: int | None = None

    @property
    def identifier(self) -> str:
        """Return the device identifier."""
        return self._device_config.identifier

    @property
    def name(self) -> str:
        """Return the device name."""
        return self._device_config.name

    @property
    def address(self) -> str | None:
        """Return the device address."""
        return self._device_config.identifier

    @property
    def is_on(self) -> bool:
        """Whether the PSN is on or off."""
        if self._psn and self._psn_data and self._psn_data.available is True:
            return True
        return False

    @property
    def log_id(self) -> str:
        """Return a log identifier for this device."""
        return self._device_config.name

    async def get_game_library(self, limit: int = 10, offset: int = 0) -> tuple[list, int | None]:
        """
        Fetch a page of the user's game library.

        :param limit: Number of titles per page.
        :param offset: 0-based start offset.
        :return: Tuple of (titles list, total count or None if unknown).
        """
        psn = self._psn
        if not psn:
            return [], self._total_game_count
        try:
            def _fetch() -> tuple[list, int]:
                iterator = psn.client.title_stats(offset=offset, page_size=limit)
                titles = list(islice(iterator, limit))
                total: int = getattr(iterator, "_total_item_count", 0)
                return titles, total

            titles, total = await self._loop.run_in_executor(None, _fetch)
            self._total_game_count = total
            return titles, self._total_game_count
        except Exception as ex:  # pylint: disable=broad-exception-caught
            _LOG.error("[%s] Error fetching game library: %s", self.log_id, ex)
            return [], self._total_game_count

    async def establish_connection(self) -> None:
        """Establish connection to PSN - called by base class connect()."""
        try:
            self._psn = PlayStationNetwork(self._device_config.npsso, self._loop)
            _LOG.debug("[%s] PSN connection established", self.log_id)
            # Do initial attribute update
            await self.poll_device()
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

        await super().disconnect()
        if self._psn:
            try:
                self._psn.close()
            except Exception as err:  # pylint: disable=broad-exception-caught
                _LOG.exception(
                    "[%s] An error occurred while disconnecting: %s", self.log_id, err
                )
            finally:
                self._psn = None

    async def poll_device(self) -> None:
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

            # Determine state based on PSN data
            if (
                self._psn_data.platform
                and self._psn_data.platform.get("platform", "")
                and self._psn_data.platform.get("onlineStatus", "") == "online"
            ):
                self.psn_state = media_player.States.ON
                if (
                    self._psn_data.available
                    and self._psn_data.title_metadata
                    and self._psn_data.title_metadata.get("npTitleId") is not None
                ):
                    self.psn_state = media_player.States.PLAYING
            else:
                self.psn_state = media_player.States.OFF

            self._state = str(self.psn_state)

            # Update title metadata
            if self._psn_data.title_metadata and self._psn_data.title_metadata.get(
                "npTitleId"
            ):
                self.psn_media_title = self._psn_data.title_metadata.get("titleName") or ""
                self.psn_media_artist = self._psn_data.title_metadata.get("format") or ""

                title = self._psn_data.title_metadata
                if title.get("format", "") == "PS5":
                    self.psn_media_image_url = title.get("conceptIconUrl") or ""
                elif title.get("format", "") == "PS4":
                    self.psn_media_image_url = title.get("npTitleIconUrl") or ""
                else:
                    self.psn_media_image_url = ""
            else:
                # Clear metadata when not playing
                self.psn_media_title = ""
                self.psn_media_artist = ""
                self.psn_media_image_url = ""

            # Notify subscribed entities
            self.push_update()

        except Exception as ex:  # pylint: disable=broad-exception-caught
            _LOG.error("[%s] Error while polling PSN: %s", self.log_id, ex)
            self.events.emit(
                DeviceEvents.ERROR,
                self.identifier,
                f"Error while polling PSN: {ex}",
            )
