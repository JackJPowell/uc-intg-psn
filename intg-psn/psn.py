"""
This module implements the PlayStation Network communication of the Remote integration driver.

:copyright: (c) 2025 by Jack Powell.
:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import asyncio
import logging
from asyncio import AbstractEventLoop
from itertools import islice
from typing import Any

import playdirector
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

        # playdirector control — credential loaded from config if ps_device is set
        self._pd_credential: playdirector.RemotePlayCredentials | None = None
        self._connect_lock = asyncio.Lock()

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

    @property
    def has_control(self) -> bool:
        """True if playdirector credentials are configured."""
        return bool(self._device_config.ps_device)

    @property
    def device_type(self) -> str:
        """Return the PlayStation device type string ('PS4', 'PS5', or 'UNKNOWN')."""
        return self._device_config.ps_device.get("device_type", "UNKNOWN")

    @property
    def pd_device(self) -> playdirector.DiscoveredDevice | None:
        """
        Build a DiscoveredDevice directly from stored config — no network lookup.

        Only device.ip and device.device_type are used by the control functions
        (wake, standby, go_home, send_buttons). The TCP port is never read when
        using RemotePlayCredentials because the remote-play session always
        connects to port 9295 internally.
        """
        ps = self._device_config.ps_device
        ip = ps.get("device_ip", "")
        raw_type = ps.get("device_type", "UNKNOWN")
        if not ip:
            return None
        try:
            device_type = playdirector.DeviceType(raw_type)
        except ValueError:
            device_type = playdirector.DeviceType.UNKNOWN
        return playdirector.DiscoveredDevice(
            ip=ip,
            port=0,  # unused — RemotePlayCredentials always connects to port 9295
            device_id=ps.get("device_id", ""),
            name=self._device_config.name,
            status=playdirector.DeviceStatus.UNKNOWN,
            device_type=device_type,
            system_version="",
        )

    async def get_game_library(
        self, limit: int = 10, offset: int = 0
    ) -> tuple[list, int | None]:
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

    async def connect(self) -> bool:
        """Establish connection, guarded against concurrent calls."""
        if self.is_connected:
            _LOG.debug(
                "[%s] Already connected, skipping duplicate connect", self.log_id
            )
            return True
        async with self._connect_lock:
            # Re-check inside the lock in case another coroutine connected first
            if self.is_connected:
                _LOG.debug(
                    "[%s] Connected while waiting for lock, skipping", self.log_id
                )
                return True
            return await super().connect()

    async def establish_connection(self) -> None:
        """Establish connection to PSN - called by base class connect()."""
        try:
            self._psn = PlayStationNetwork(self._device_config.npsso, self._loop)
            _LOG.debug("[%s] PSN connection established", self.log_id)

            # Load playdirector credentials if configured
            if self._device_config.ps_device:
                try:
                    self._pd_credential = playdirector.RemotePlayCredentials.from_dict(
                        self._device_config.ps_device
                    )
                    _LOG.info(
                        "[%s] playdirector: credentials loaded for %s (%s)",
                        self.log_id,
                        self._device_config.ps_device.get("device_ip"),
                        self.device_type,
                    )
                except Exception as ex:  # pylint: disable=broad-exception-caught
                    _LOG.warning(
                        "[%s] playdirector: failed to load credentials: %s",
                        self.log_id,
                        ex,
                    )

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

    # ------------------------------------------------------------------ #
    # playdirector control methods
    # ------------------------------------------------------------------ #

    async def power_on(self) -> None:
        """Wake the PlayStation from standby."""
        if not self._pd_credential:
            _LOG.warning("[%s] power_on: no credentials configured", self.log_id)
            return
        device = self.pd_device
        if not device:
            _LOG.warning("[%s] power_on: no device IP configured", self.log_id)
            return
        try:
            await playdirector.wake(device, self._pd_credential)
            _LOG.debug("[%s] power_on: wake sent", self.log_id)
        except Exception as ex:  # pylint: disable=broad-exception-caught
            _LOG.error("[%s] power_on failed: %s", self.log_id, ex)

    async def power_off(self) -> None:
        """Put the PlayStation into standby."""
        if not self._pd_credential:
            _LOG.warning("[%s] power_off: no credentials configured", self.log_id)
            return
        device = self.pd_device
        if not device:
            _LOG.warning("[%s] power_off: no device IP configured", self.log_id)
            return
        try:
            await playdirector.standby(device, self._pd_credential)
            _LOG.debug("[%s] power_off: standby sent", self.log_id)
        except Exception as ex:  # pylint: disable=broad-exception-caught
            _LOG.error("[%s] power_off failed: %s", self.log_id, ex)

    async def power_toggle(self) -> None:
        """Toggle the PlayStation power state based on current psn_state."""
        if self.psn_state in (media_player.States.ON, media_player.States.PLAYING):
            await self.power_off()
        else:
            await self.power_on()

    async def go_home(self) -> None:
        """Navigate to the PS5 home screen."""
        if not self._pd_credential:
            _LOG.warning("[%s] go_home: no credentials configured", self.log_id)
            return
        device = self.pd_device
        if not device:
            _LOG.warning("[%s] go_home: no device IP configured", self.log_id)
            return
        try:
            await playdirector.go_home(device, self._pd_credential)
            _LOG.debug("[%s] go_home: sent", self.log_id)
        except Exception as ex:  # pylint: disable=broad-exception-caught
            _LOG.error("[%s] go_home failed: %s", self.log_id, ex)

    async def send_buttons(
        self,
        operations: list[Any],
        hold_time: float = 0.1,
    ) -> None:
        """Send RemoteOperation button presses to a PS4."""
        if not self._pd_credential:
            _LOG.warning("[%s] send_buttons: no credentials configured", self.log_id)
            return
        device = self.pd_device
        if not device:
            _LOG.warning("[%s] send_buttons: no device IP configured", self.log_id)
            return
        try:
            await playdirector.send_buttons(
                device, self._pd_credential, operations, hold_time=int(hold_time * 1000)
            )
            _LOG.debug("[%s] send_buttons: %s sent", self.log_id, operations)
        except Exception as ex:  # pylint: disable=broad-exception-caught
            _LOG.error("[%s] send_buttons failed: %s", self.log_id, ex)

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
                self.psn_media_title = (
                    self._psn_data.title_metadata.get("titleName") or ""
                )
                self.psn_media_artist = (
                    self._psn_data.title_metadata.get("format") or ""
                )

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
