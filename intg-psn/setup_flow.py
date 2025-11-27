"""
Setup flow for PlayStation Network integration.

:copyright: (c) 2025 by Jack Powell.
:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import logging
from typing import Any
from driver import PSNDevice
from psn import PlaystationNetwork
from psnawp_api.utils.misc import parse_npsso_token
from ucapi import RequestUserInput
from ucapi_framework import BaseSetupFlow

_LOG = logging.getLogger(__name__)


class PSNSetupFlow(BaseSetupFlow[PSNDevice]):
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
                                    "Your NPSSO Token is required to authenticate to the PlayStation Network. "
                                    "\n\nPlease sign in to the [PlayStation Network](https://playstation.com) first. "
                                    "Then [click here](https://ca.account.sony.com/api/v1/ssocookie) to retrieve your token."
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
    ) -> RequestUserInput | PSNDevice:
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
            psnawp = PlaystationNetwork(npsso)
            user = psnawp.get_user()
            _LOG.info("Authenticated PSN Account: %s", user.online_id)

            return PSNDevice(
                identifier=user.account_id, name=user.online_id, npsso=npsso
            )

        except Exception as err:
            _LOG.error("Failed to authenticate with PSN: %s", err)
            raise ValueError(f"Failed to authenticate with PSN: {err}") from err
