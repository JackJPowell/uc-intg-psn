"""
This module implements the PlayStation Network communication of the Remote integration driver.

:copyright: (c) 2025 by Jack Powell.
:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import logging
from asyncio import AbstractEventLoop

from api import PlayStationNetwork, PlayStationNetworkData
from const import PSNConfig
from psnawp_api.core.psnawp_exceptions import PSNAWPAuthenticationError
from ucapi import media_player
from ucapi.entity import EntityTypes
from ucapi_framework import (
    BaseIntegrationDriver,
    Entity,
    MediaPlayerAttributes,
    create_entity_id,
)
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

        # Initialize attributes dataclass for entity state
        self.attrs = MediaPlayerAttributes(
            STATE=media_player.States.UNKNOWN,
            MEDIA_IMAGE_URL="",
            MEDIA_TITLE="",
            MEDIA_ARTIST="",
        )

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

    def get_device_attributes(self, entity_id: str) -> MediaPlayerAttributes:
        """
        Get current device attributes as a dataclass.

        This method is called by the framework to retrieve entity state.

        :param entity_id: The entity ID requesting attributes
        :return: MediaPlayerAttributes dataclass with current state
        """
        return self.attrs

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

            # Update attributes dataclass based on PSN data
            if (
                self._psn_data.platform
                and self._psn_data.platform.get("platform", "")
                and self._psn_data.platform.get("onlineStatus", "") == "online"
            ):
                self.attrs.STATE = media_player.States.ON
                if (
                    self._psn_data.available
                    and self._psn_data.title_metadata
                    and self._psn_data.title_metadata.get("npTitleId") is not None
                ):
                    self.attrs.STATE = media_player.States.PLAYING
            else:
                self.attrs.STATE = media_player.States.OFF

            self._state = str(self.attrs.STATE)

            # Add title metadata if available
            if self._psn_data.title_metadata and self._psn_data.title_metadata.get(
                "npTitleId"
            ):
                self.attrs.MEDIA_TITLE = self._psn_data.title_metadata.get("titleName")
                self.attrs.MEDIA_ARTIST = self._psn_data.title_metadata.get("format")

                title = self._psn_data.title_metadata
                if title.get("format", "") == "PS5":
                    self.attrs.MEDIA_IMAGE_URL = title.get("conceptIconUrl")
                elif title.get("format", "") == "PS4":
                    self.attrs.MEDIA_IMAGE_URL = title.get("npTitleIconUrl")
            else:
                # Clear metadata when not playing
                self.attrs.MEDIA_TITLE = ""
                self.attrs.MEDIA_ARTIST = ""
                self.attrs.MEDIA_IMAGE_URL = ""

            # Get entity reference and update it directly
            if self._driver:
                entity_id = create_entity_id(EntityTypes.MEDIA_PLAYER, self.identifier)
                entity = self._driver.get_entity_by_id(entity_id)

                # Type hint for the entity to access update method
                if entity and isinstance(entity, Entity):
                    # Entity has the framework's update method
                    entity.update(self.attrs)

        except Exception as ex:  # pylint: disable=broad-exception-caught
            _LOG.error("[%s] Error while polling PSN: %s", self.log_id, ex)
            self.events.emit(
                DeviceEvents.ERROR,
                self.identifier,
                f"Error while polling PSN: {ex}",
            )
