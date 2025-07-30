"""
Setup flow for Apple TV Remote integration.

:copyright: (c) 2023-2024 by Unfolded Circle ApS.
:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import asyncio
import logging
from enum import IntEnum

import config
from config import PSNDevice
from ucapi import (
    AbortDriverSetup,
    DriverSetupRequest,
    IntegrationSetupError,
    RequestUserInput,
    SetupAction,
    SetupComplete,
    SetupDriver,
    SetupError,
    UserDataResponse,
)
from psn import PlaystationNetwork
from psnawp_api.utils.misc import parse_npsso_token

_LOG = logging.getLogger(__name__)


class SetupSteps(IntEnum):
    """Enumeration of setup steps to keep track of user data responses."""

    INIT = 0
    CONFIGURATION_MODE = 1
    DISCOVER = 2
    DEVICE_CHOICE = 3


_setup_step = SetupSteps.INIT
_cfg_add_device: bool = False

_user_input_discovery = RequestUserInput(
    {"en": "PlayStationNetwork Setup"},
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
                            "Your NPSSO Token is required to authenticate to the PlayStation Network. \
                            \n\nPlease signin to the [Playstation Network](https://playstation.com) first. \
                            Then [click here](https://ca.account.sony.com/api/v1/ssocookie) to retrieve your token."
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


async def driver_setup_handler(
    msg: SetupDriver,
) -> SetupAction:  # pylint: disable=too-many-return-statements
    """
    Dispatch driver setup requests to corresponding handlers.

    Either start the setup process or handle the selected Apple TV device.

    :param msg: the setup driver request object, either DriverSetupRequest or UserDataResponse
    :return: the setup action on how to continue
    """
    global _setup_step
    global _cfg_add_device

    if isinstance(msg, DriverSetupRequest):
        _setup_step = SetupSteps.INIT
        _cfg_add_device = False
        return await _handle_driver_setup(msg)

    if isinstance(msg, UserDataResponse):
        _LOG.debug("%s", msg)
        if (
            _setup_step == SetupSteps.CONFIGURATION_MODE
            and "action" in msg.input_values
        ):
            return await _handle_configuration_mode(msg)
        if _setup_step == SetupSteps.DISCOVER and "npsso" in msg.input_values:
            return await _handle_discovery(msg)
        _LOG.error("No or invalid user response was received: %s", msg)
    elif isinstance(msg, AbortDriverSetup):
        _LOG.info("Setup was aborted with code: %s", msg.error)
        _setup_step = SetupSteps.INIT

    return SetupError()


async def _handle_driver_setup(
    msg: DriverSetupRequest,
) -> RequestUserInput | SetupError:
    """
    Start driver setup.

    Initiated by Remote Two to set up the driver. The reconfigure flag determines the setup flow:

    - Reconfigure is True: show the configured devices and ask user what action to perform (add, delete, reset).
    - Reconfigure is False: clear the existing configuration and show device discovery screen.
      Ask user to enter ip-address for manual configuration, otherwise auto-discovery is used.

    :param msg: driver setup request data, only `reconfigure` flag is of interest.
    :return: the setup action on how to continue
    """
    global _setup_step

    reconfigure = msg.reconfigure
    _LOG.debug("Starting driver setup, reconfigure=%s", reconfigure)

    if reconfigure:
        _setup_step = SetupSteps.CONFIGURATION_MODE

        # get all configured devices for the user to choose from
        dropdown_devices = []
        for device in config.devices.all():
            dropdown_devices.append(
                {"id": device.identifier, "label": {"en": f"{device.name}"}}
            )

        dropdown_actions = [
            {
                "id": "add",
                "label": {
                    "en": "Add a new PSN Account",
                },
            },
        ]

        # add remove & reset actions if there's at least one configured device
        if dropdown_devices:
            dropdown_actions.append(
                {
                    "id": "npsso",
                    "label": {
                        "en": "Update NPSSO Token for selected PSN Account",
                    },
                },
            )
            dropdown_actions.append(
                {
                    "id": "remove",
                    "label": {
                        "en": "Delete selected PSN Account",
                    },
                },
            )
            dropdown_actions.append(
                {
                    "id": "reset",
                    "label": {
                        "en": "Reset configuration and reconfigure",
                        "de": "Konfiguration zurücksetzen und neu konfigurieren",
                        "fr": "Réinitialiser la configuration et reconfigurer",
                    },
                },
            )
        else:
            # dummy entry if no devices are available
            dropdown_devices.append({"id": "", "label": {"en": "---"}})

        return RequestUserInput(
            {"en": "Configuration mode", "de": "Konfigurations-Modus"},
            [
                {
                    "field": {
                        "dropdown": {
                            "value": dropdown_devices[0]["id"],
                            "items": dropdown_devices,
                        }
                    },
                    "id": "choice",
                    "label": {
                        "en": "Configured Accounts",
                    },
                },
                {
                    "field": {
                        "dropdown": {
                            "value": dropdown_actions[0]["id"],
                            "items": dropdown_actions,
                        }
                    },
                    "id": "action",
                    "label": {
                        "en": "Action",
                        "de": "Aktion",
                        "fr": "Appareils configurés",
                    },
                },
            ],
        )

    # Initial setup, make sure we have a clean configuration
    config.devices.clear()  # triggers device instance removal
    _setup_step = SetupSteps.DISCOVER
    return _user_input_discovery


async def _handle_configuration_mode(
    msg: UserDataResponse,
) -> RequestUserInput | SetupComplete | SetupError:
    """
    Process user data response from the configuration mode screen.

    User input data:

    - ``choice`` contains identifier of selected device
    - ``action`` contains the selected action identifier

    :param msg: user input data from the configuration mode screen.
    :return: the setup action on how to continue
    """
    global _setup_step
    global _cfg_add_device

    action = msg.input_values["action"]

    # workaround for web-configurator not picking up first response
    await asyncio.sleep(1)

    match action:
        case "add":
            _cfg_add_device = True
        case "npsso":
            choice = msg.input_values["choice"]
            if not config.devices.remove(choice):
                _LOG.warning("Could not remove device from configuration: %s", choice)
                return SetupError(error_type=IntegrationSetupError.OTHER)
            _setup_step = SetupSteps.DISCOVER
            return _user_input_discovery
        case "remove":
            choice = msg.input_values["choice"]
            if not config.devices.remove(choice):
                _LOG.warning("Could not remove device from configuration: %s", choice)
                return SetupError(error_type=IntegrationSetupError.OTHER)
            config.devices.store()
            return SetupComplete()
        case "reset":
            config.devices.clear()  # triggers device instance removal
        case _:
            _LOG.error("Invalid configuration action: %s", action)
            return SetupError(error_type=IntegrationSetupError.OTHER)

    _setup_step = SetupSteps.DISCOVER
    return _user_input_discovery


async def _handle_discovery(msg: UserDataResponse) -> RequestUserInput | SetupError:
    """
    Process user data response from the first setup process screen.

    :param msg: response data from the requested user data
    :return: the setup action on how to continue
    """
    npsso = parse_npsso_token(msg.input_values["npsso"])

    if npsso:
        _LOG.debug("Connecting to PSN API")

        psnawp = PlaystationNetwork(npsso)
        user = psnawp.get_user()
        _LOG.info("Account: %s", user.online_id)

    # if we are adding a new device: make sure it's not already configured
    if _cfg_add_device and config.devices.contains(user.account_id):
        _LOG.info("Skipping found device %s: already configured", user.online_id)
        return SetupError(error_type=IntegrationSetupError.OTHER)

    device = PSNDevice(user.account_id, user.online_id, npsso)
    config.devices.add_or_update(device)

    await asyncio.sleep(1)

    _LOG.info("Setup successfully completed for %s", device.name)

    return SetupComplete()
