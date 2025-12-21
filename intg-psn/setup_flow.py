"""
Setup flow for PlayStation Network integration.

:copyright: (c) 2025 by Jack Powell.
:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import logging
from typing import Any

from api import PlayStationNetwork
from const import PSNConfig
from psnawp_api.utils.misc import parse_npsso_token
from ucapi import RequestUserInput
from ucapi_framework import BaseSetupFlow, MigrationData, EntityMigrationMapping
from packaging.version import Version

_LOG = logging.getLogger(__name__)


class PSNSetupFlow(BaseSetupFlow[PSNConfig]):
    """
    Setup flow for PlayStation Network integration.

    Handles PSN account configuration through NPSSO token authentication.
    PSN does not support device discovery, so manual entry is always required.
    """

    def get_manual_entry_form(self) -> RequestUserInput:
        """
        Get the NPSSO token entry form.

        :return: RequestUserInput for NPSSO token
        """
        return RequestUserInput(
            {"en": "PlayStation Network Setup"},
            [
                {
                    "id": "info",
                    "label": {
                        "en": "Supply your NPSSO Token",
                    },
                    "field": {
                        "label": {
                            "value": {
                                "en": (
                                    "Your NPSSO Token is required to authenticate "
                                    "to the PlayStation Network. "
                                    "\n\nPlease sign in to the "
                                    "[PlayStation Network](https://playstation.com) first. "
                                    "Then [click here]"
                                    "(https://ca.account.sony.com/api/v1/ssocookie) "
                                    "to retrieve your token."
                                ),
                            }
                        }
                    },
                },
                {
                    "field": {"text": {"value": ""}},
                    "id": "npsso",
                    "label": {
                        "en": "NPSSO Token",
                    },
                },
            ],
        )

    async def query_device(
        self, input_values: dict[str, Any]
    ) -> RequestUserInput | PSNConfig:
        """
        Create PSN device configuration from manual NPSSO token entry.

        :param input_values: User input containing 'npsso' token
        :return: PSN device configuration
        :raises ValueError: If authentication fails
        """
        npsso = parse_npsso_token(input_values.get("npsso", ""))

        if not npsso:
            _LOG.error("Invalid or missing NPSSO token")
            raise ValueError("Invalid or missing NPSSO token")
        try:
            _LOG.debug("Connecting to PSN API")
            psnawp = PlayStationNetwork(npsso, self.driver.loop)
            user = psnawp.get_user()
            _LOG.info("Authenticated PSN Account: %s", user.online_id)

            return PSNConfig(
                identifier=user.account_id, name=user.online_id, npsso=npsso
            )

        except Exception as err:
            _LOG.error("Failed to authenticate with PSN: %s", err)
            raise ValueError(f"Failed to authenticate with PSN: {err}") from err

    async def is_migration_required(self, previous_version: str) -> bool:
        """
        Check if migration is required for existing configuration.

        :param previous_version: Previous version of the integration
        :return: True if migration is required, False otherwise
        """
        # Migrations required for versions 1.0.2 and below
        if Version(previous_version) <= Version("1.0.2"):
            return True
        return False

    async def get_migration_data(
        self, previous_version: str, current_version: str
    ) -> MigrationData:
        """Generate entity ID mappings for migration.

        Returns:
            MigrationData with driver IDs and entity mappings
        """

        mappings: list[EntityMigrationMapping] = []

        # Iterate through all configured devices
        for device in self.config.all():
            mappings.append(
                {
                    "previous_entity_id": f"{device.identifier}",
                    "new_entity_id": f"media_player.{device.identifier}",
                }
            )

        return {
            "previous_driver_id": "psn_driver",
            "new_driver_id": "psn_driver",
            "entity_mappings": mappings,
        }
