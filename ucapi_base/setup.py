"""
Base setup flow for Unfolded Circle Remote integrations.

Provides reusable setup flow logic for device configuration.

:copyright: (c) 2025 by Jack Powell.
:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import asyncio
import logging
from abc import ABC, abstractmethod
from enum import IntEnum
from typing import Any, Generic, TypeVar

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

from .discovery import DiscoveredDevice
from .config import BaseDeviceManager

_LOG = logging.getLogger(__name__)

# Type variable for device configuration
ConfigT = TypeVar("ConfigT")


class SetupSteps(IntEnum):
    """Enumeration of setup steps to keep track of user data responses."""

    INIT = 0
    CONFIGURATION_MODE = 1
    PRE_DISCOVERY = 2
    DISCOVER = 3
    DEVICE_CHOICE = 4
    MANUAL_ENTRY = 5
    BACKUP = 6
    RESTORE = 7


class BaseSetupFlow(ABC, Generic[ConfigT]):
    """
    Base class for integration setup flows.

    Handles common patterns:
    - Configuration mode (add/update/remove/reset)
    - Device discovery with manual fallback
    - Device creation and validation
    - State machine management

    Type Parameters:
        ConfigT: The device configuration class
    """

    def __init__(self, config_manager: BaseDeviceManager, discovery_class=None):
        """
        Initialize the setup flow.

        :param config_manager: Device configuration manager instance
        :param discovery_class: Optional discovery class for auto-discovery
        """
        self.config = config_manager
        self.discovery = discovery_class
        self._setup_step = SetupSteps.INIT
        self._add_mode = False
        self._pending_device_config: ConfigT | None = None  # For multi-screen flows
        self._pre_discovery_data: dict[str, Any] = {}  # Store data from pre-discovery screens

    @classmethod
    def create_handler(cls, config_manager, discovery_class=None):
        """
        Create a setup handler function with the given configuration.

        This is a convenience factory method that creates a closure containing
        the setup flow instance, suitable for passing to IntegrationAPI.init().

        :param config_manager: Device configuration manager instance
        :param discovery_class: Optional discovery class for auto-discovery
        :return: Async function that handles SetupDriver messages
        """
        setup_flow = None

        async def driver_setup_handler(msg: SetupDriver):
            """Handle driver setup requests."""
            nonlocal setup_flow

            if setup_flow is None:
                _LOG.info("Creating new %s instance", cls.__name__)
                setup_flow = cls(config_manager, discovery_class)

            return await setup_flow.handle_driver_setup(msg)

        return driver_setup_handler

    async def handle_driver_setup(self, msg: SetupDriver) -> SetupAction:
        """
        Main dispatcher for setup requests.

        :param msg: Setup driver request object
        :return: Setup action on how to continue
        """
        if isinstance(msg, DriverSetupRequest):
            self._setup_step = SetupSteps.INIT
            self._add_mode = False
            return await self._handle_driver_setup_request(msg)

        if isinstance(msg, UserDataResponse):
            _LOG.debug("User data response: %s", msg)
            return await self._handle_user_data_response(msg)

        if isinstance(msg, AbortDriverSetup):
            _LOG.info("Setup was aborted with code: %s", msg.error)
            self._setup_step = SetupSteps.INIT

        return SetupError()

    async def _handle_driver_setup_request(
        self, msg: DriverSetupRequest
    ) -> RequestUserInput | SetupError:
        """
        Handle initial setup request.

        :param msg: Driver setup request
        :return: Setup action
        """
        reconfigure = msg.reconfigure
        _LOG.debug("Starting driver setup, reconfigure=%s", reconfigure)

        if reconfigure:
            self._setup_step = SetupSteps.CONFIGURATION_MODE
            return await self._build_configuration_mode_screen()

        # Initial setup - clear configuration and start discovery
        self.config.clear()
        self._pre_discovery_data = {}
        
        # Check if pre-discovery screen is needed
        pre_discovery_screen = await self.get_pre_discovery_screen()
        if pre_discovery_screen is not None:
            self._setup_step = SetupSteps.PRE_DISCOVERY
            return pre_discovery_screen
        
        # No pre-discovery needed, go straight to discovery
        self._setup_step = SetupSteps.DISCOVER
        return await self._handle_discovery()

    async def _handle_user_data_response(self, msg: UserDataResponse) -> SetupAction:
        """
        Route user data responses to appropriate handlers.

        :param msg: User data response
        :return: Setup action
        """
        # Check if we're in an additional configuration flow
        if self._pending_device_config is not None:
            return await self._handle_additional_configuration_response(msg)

        if (
            self._setup_step == SetupSteps.CONFIGURATION_MODE
            and "action" in msg.input_values
        ):
            return await self._handle_configuration_mode(msg)

        if self._setup_step == SetupSteps.PRE_DISCOVERY:
            return await self._handle_pre_discovery_response(msg)

        if self._setup_step == SetupSteps.DISCOVER and "choice" in msg.input_values:
            choice = msg.input_values["choice"]
            if choice == "manual":
                return await self._handle_manual_entry()
            return await self._handle_device_selection(msg)

        if self._setup_step == SetupSteps.MANUAL_ENTRY:
            return await self._handle_manual_entry_response(msg)

        if self._setup_step == SetupSteps.BACKUP:
            # User has seen the backup, complete setup
            _LOG.info("Backup completed, finishing setup")
            return SetupComplete()

        if self._setup_step == SetupSteps.RESTORE:
            return await self._handle_restore_response(msg)

        _LOG.error("No handler for user input in step: %s", self._setup_step)
        return SetupError()

    async def _build_configuration_mode_screen(self) -> RequestUserInput:
        """
        Build the configuration mode screen.

        Shows configured devices and available actions (add/update/remove/reset).
        """
        dropdown_devices = []
        for device in self.config.all():
            device_id = self.get_device_id(device)
            device_name = self.get_device_name(device)
            dropdown_devices.append({"id": device_id, "label": {"en": device_name}})

        dropdown_actions = [
            {
                "id": "add",
                "label": {"en": "Add a new device"},
            },
        ]

        # Add update/remove/reset/backup/restore actions if devices exist
        if dropdown_devices:
            dropdown_actions.extend(
                [
                    {
                        "id": "update",
                        "label": {"en": "Update information for selected device"},
                    },
                    {
                        "id": "remove",
                        "label": {"en": "Remove selected device"},
                    },
                    {
                        "id": "reset",
                        "label": {"en": "Reset configuration and reconfigure"},
                    },
                    {
                        "id": "backup",
                        "label": {"en": "Backup configuration to clipboard"},
                    },
                    {
                        "id": "restore",
                        "label": {"en": "Restore configuration from backup"},
                    },
                ]
            )
        else:
            # Dummy entry if no devices
            dropdown_devices.append({"id": "", "label": {"en": "---"}})
            # Still allow restore even if no devices
            dropdown_actions.append(
                {
                    "id": "restore",
                    "label": {"en": "Restore configuration from backup"},
                }
            )

        return RequestUserInput(
            {"en": "Configuration mode"},
            [
                {
                    "field": {
                        "dropdown": {
                            "value": dropdown_devices[0]["id"],
                            "items": dropdown_devices,
                        }
                    },
                    "id": "choice",
                    "label": {"en": "Configured Devices"},
                },
                {
                    "field": {
                        "dropdown": {
                            "value": dropdown_actions[0]["id"],
                            "items": dropdown_actions,
                        }
                    },
                    "id": "action",
                    "label": {"en": "Action"},
                },
            ],
        )

    async def _handle_configuration_mode(self, msg: UserDataResponse) -> SetupAction:
        """
        Process configuration mode action selection.

        :param msg: User data response
        :return: Setup action
        """
        action = msg.input_values["action"]

        # Workaround for web-configurator not picking up first response
        await asyncio.sleep(1)

        match action:
            case "add":
                self._add_mode = True
                self._pre_discovery_data = {}
                
                # Check if pre-discovery screen is needed
                pre_discovery_screen = await self.get_pre_discovery_screen()
                if pre_discovery_screen is not None:
                    self._setup_step = SetupSteps.PRE_DISCOVERY
                    return pre_discovery_screen
                
                self._setup_step = SetupSteps.DISCOVER
                return await self._handle_discovery()

            case "update":
                choice = msg.input_values["choice"]
                if not self.config.remove(choice):
                    _LOG.warning("Could not update device: %s", choice)
                    return SetupError(error_type=IntegrationSetupError.OTHER)
                
                self._pre_discovery_data = {}
                
                # Check if pre-discovery screen is needed
                pre_discovery_screen = await self.get_pre_discovery_screen()
                if pre_discovery_screen is not None:
                    self._setup_step = SetupSteps.PRE_DISCOVERY
                    return pre_discovery_screen
                
                self._setup_step = SetupSteps.DISCOVER
                return await self._handle_discovery()

            case "remove":
                choice = msg.input_values["choice"]
                if not self.config.remove(choice):
                    _LOG.warning("Could not remove device: %s", choice)
                    return SetupError(error_type=IntegrationSetupError.OTHER)
                self.config.store()
                return SetupComplete()

            case "reset":
                self.config.clear()
                self._pre_discovery_data = {}
                
                # Check if pre-discovery screen is needed
                pre_discovery_screen = await self.get_pre_discovery_screen()
                if pre_discovery_screen is not None:
                    self._setup_step = SetupSteps.PRE_DISCOVERY
                    return pre_discovery_screen
                
                self._setup_step = SetupSteps.DISCOVER
                return await self._handle_discovery()

            case "backup":
                return await self._handle_backup()

            case "restore":
                return await self._handle_restore()

            case _:
                _LOG.error("Invalid configuration action: %s", action)
                return SetupError(error_type=IntegrationSetupError.OTHER)

    async def _handle_pre_discovery_response(
        self, msg: UserDataResponse
    ) -> SetupAction:
        """
        Internal handler for pre-discovery screens.

        Calls the overridable handle_pre_discovery_response and proceeds to
        discovery if it returns None, or shows another screen if returned.

        :param msg: User data response
        :return: Setup action
        """
        try:
            # Store the input values
            self._pre_discovery_data.update(msg.input_values)

            # Call the overridable method
            result = await self.handle_pre_discovery_response(msg)

            # If it returns a screen, show it
            if result is not None:
                return result

            # If it returns None, proceed to discovery
            self._setup_step = SetupSteps.DISCOVER
            return await self._handle_discovery()

        except Exception as err:  # pylint: disable=broad-except
            _LOG.error("Error in pre-discovery configuration: %s", err)
            self._pre_discovery_data = {}
            return SetupError(error_type=IntegrationSetupError.OTHER)

    async def _handle_discovery(self) -> RequestUserInput:
        """
        Handle device discovery.

        Attempts auto-discovery if available, otherwise shows manual entry.
        """
        self._setup_step = SetupSteps.DISCOVER

        if self.discovery is None:
            # No discovery available, go straight to manual entry
            return await self._handle_manual_entry()

        # Attempt discovery
        discovered_devices = await self.discover_devices()

        if discovered_devices:
            _LOG.debug("Found %d device(s)", len(discovered_devices))

            dropdown_devices = []
            for device in discovered_devices:
                dropdown_devices.append(
                    {
                        "id": device.identifier,
                        "label": {"en": f"{device.name} ({device.address})"},
                    }
                )

            # Add manual entry option
            dropdown_devices.append({"id": "manual", "label": {"en": "Setup Manually"}})

            fields = [
                {
                    "field": {
                        "dropdown": {
                            "value": dropdown_devices[0]["id"],
                            "items": dropdown_devices,
                        }
                    },
                    "id": "choice",
                    "label": {"en": "Discovered Devices"},
                }
            ]

            # Add any additional discovery fields
            fields.extend(self.get_additional_discovery_fields())

            return RequestUserInput({"en": "Discovered Devices"}, fields)

        # No devices found, show manual entry
        return await self._handle_manual_entry()

    async def _finalize_device_setup(
        self, device_config: ConfigT, input_values: dict[str, Any]
    ) -> SetupComplete | SetupError | RequestUserInput:
        """
        Common logic to finalize device setup after creation.

        Checks for duplicates, handles additional configuration screens,
        and saves the device configuration.

        :param device_config: Device configuration to finalize
        :param input_values: User input values from the previous screen
        :return: Setup action
        """
        # Check for duplicates in add mode
        if self._add_mode and self.config.contains(self.get_device_id(device_config)):
            _LOG.warning(
                "Device already configured: %s", self.get_device_id(device_config)
            )
            return SetupError(error_type=IntegrationSetupError.OTHER)

        # Store pending config and check if additional configuration needed
        self._pending_device_config = device_config
        additional_screen = await self.get_additional_configuration_screen(
            device_config, input_values
        )
        if additional_screen is not None:
            return additional_screen

        # No additional screens, save and complete
        self.config.add_or_update(self._pending_device_config)
        self._pending_device_config = None

        await asyncio.sleep(1)
        _LOG.info("Setup completed for %s", self.get_device_name(device_config))
        return SetupComplete()

    async def _handle_device_selection(
        self, msg: UserDataResponse
    ) -> SetupComplete | SetupError | RequestUserInput:
        """
        Handle user selecting a discovered device.

        :param msg: User data response
        :return: Setup action
        """
        device_id = msg.input_values.get("choice")
        if not device_id:
            return SetupError(error_type=IntegrationSetupError.NOT_FOUND)

        # Extract additional input values
        additional_data = self.extract_additional_setup_data(msg.input_values)

        # Create device from discovery
        try:
            device_config = await self.create_device_from_discovery(
                device_id, additional_data
            )
            return await self._finalize_device_setup(device_config, msg.input_values)

        except Exception as err:  # pylint: disable=broad-except
            _LOG.error("Setup error: %s", err)
            self._pending_device_config = None
            return SetupError(error_type=IntegrationSetupError.NOT_FOUND)

    async def _handle_manual_entry(self) -> RequestUserInput:
        """Show manual entry form."""
        self._setup_step = SetupSteps.MANUAL_ENTRY
        return self.get_manual_entry_form()

    async def _handle_manual_entry_response(
        self, msg: UserDataResponse
    ) -> SetupComplete | SetupError | RequestUserInput:
        """
        Handle manual entry form submission.

        :param msg: User data response
        :return: Setup action
        """
        try:
            device_config = await self.create_device_from_manual_entry(msg.input_values)
            return await self._finalize_device_setup(device_config, msg.input_values)

        except Exception as err:  # pylint: disable=broad-except
            _LOG.error("Setup error: %s", err)
            self._pending_device_config = None
            return SetupError(error_type=IntegrationSetupError.NOT_FOUND)

    async def _handle_additional_configuration_response(
        self, msg: UserDataResponse
    ) -> SetupAction:
        """
        Internal handler for additional configuration screens.

        Calls the overridable handle_additional_configuration_response and
        finalizes setup if it returns None.

        :param msg: User data response
        :return: Setup action
        """
        try:
            # Call the overridable method
            result = await self.handle_additional_configuration_response(msg)

            # If it returns a screen, show it
            if result is not None:
                return result

            # If it returns None, finalize the setup
            if self._pending_device_config is None:
                _LOG.error("Pending device config is None during finalization")
                return SetupError(error_type=IntegrationSetupError.OTHER)

            # Save the device and complete
            self.config.add_or_update(self._pending_device_config)
            device_name = self.get_device_name(self._pending_device_config)
            self._pending_device_config = None

            await asyncio.sleep(1)
            _LOG.info("Setup completed for %s", device_name)
            return SetupComplete()

        except Exception as err:  # pylint: disable=broad-except
            _LOG.error("Error in additional configuration: %s", err)
            self._pending_device_config = None
            return SetupError(error_type=IntegrationSetupError.OTHER)

    async def _handle_backup(self) -> RequestUserInput:
        """
        Handle backup configuration request.

        Reads the configuration JSON and displays it to the user for copying.
        """
        _LOG.info("Backing up configuration")
        self._setup_step = SetupSteps.BACKUP

        try:
            # Get the configuration as JSON string
            config_json = self.config.get_backup_json()

            return RequestUserInput(
                {"en": "Configuration Backup"},
                [
                    {
                        "id": "info",
                        "label": {"en": "Configuration Backup"},
                        "field": {
                            "label": {
                                "value": {
                                    "en": "Copy the configuration data below and save it in a safe place. "
                                    "You can use this to restore your configuration after an integration update."
                                }
                            }
                        },
                    },
                    {
                        "id": "backup_data",
                        "label": {"en": "Configuration Data (copy this)"},
                        "field": {"textarea": {"value": config_json}},
                    },
                ],
            )
        except Exception as err:  # pylint: disable=broad-except
            _LOG.error("Backup error: %s", err)
            return SetupError(error_type=IntegrationSetupError.OTHER)

    async def _handle_restore(self) -> RequestUserInput:
        """
        Handle restore configuration request.

        Prompts the user to paste their backup JSON.
        """
        _LOG.info("Starting configuration restore")
        self._setup_step = SetupSteps.RESTORE

        return RequestUserInput(
            {"en": "Restore Configuration"},
            [
                {
                    "id": "info",
                    "label": {"en": "Restore Configuration"},
                    "field": {
                        "label": {
                            "value": {
                                "en": "Paste the configuration backup data below to restore your devices."
                            }
                        }
                    },
                },
                {
                    "id": "restore_data",
                    "label": {"en": "Configuration Backup Data"},
                    "field": {"textarea": {"value": ""}},
                },
            ],
        )

    async def _handle_restore_response(
        self, msg: UserDataResponse
    ) -> SetupComplete | SetupError:
        """
        Handle restore configuration form submission.

        :param msg: User data response containing backup JSON
        :return: Setup action
        """
        try:
            restore_data = msg.input_values.get("restore_data", "").strip()

            if not restore_data:
                _LOG.error("No restore data provided")
                return SetupError(error_type=IntegrationSetupError.OTHER)

            # Restore the configuration from JSON
            success = self.config.restore_from_backup_json(restore_data)

            if not success:
                _LOG.error("Failed to restore configuration")
                return SetupError(error_type=IntegrationSetupError.OTHER)

            await asyncio.sleep(1)
            _LOG.info("Configuration restored successfully")
            return SetupComplete()

        except Exception as err:  # pylint: disable=broad-except
            _LOG.error("Restore error: %s", err)
            return SetupError(error_type=IntegrationSetupError.OTHER)

    # ========================================================================
    # Abstract Methods (Must be implemented by subclasses)
    # ========================================================================

    @abstractmethod
    async def discover_devices(self) -> list[DiscoveredDevice]:
        """
        Perform device discovery.

        :return: List of discovered devices
        """

    @abstractmethod
    async def create_device_from_discovery(
        self, device_id: str, additional_data: dict[str, Any]
    ) -> ConfigT:
        """
        Create device configuration from discovered device.

        :param device_id: Discovered device identifier
        :param additional_data: Additional user input data
        :return: Device configuration
        """

    @abstractmethod
    async def create_device_from_manual_entry(
        self, input_values: dict[str, Any]
    ) -> ConfigT:
        """
        Create device configuration from manual entry.

        :param input_values: User input values
        :return: Device configuration
        """

    @abstractmethod
    def get_manual_entry_form(self) -> RequestUserInput:
        """
        Get the manual entry form.

        :return: RequestUserInput with manual entry fields
        """

    # ========================================================================
    # Optional Override Methods
    # ========================================================================

    def get_device_id(self, device_config: ConfigT) -> str:
        """
        Extract device ID from configuration.

        Default implementation: tries common attribute names (identifier, id, device_id).
        Override this if your config uses a different attribute name.

        :param device_config: Device configuration
        :return: Device identifier
        :raises AttributeError: If no valid ID attribute is found
        """
        for attr in ("identifier", "id", "device_id"):
            if hasattr(device_config, attr):
                value = getattr(device_config, attr)
                if value:
                    return str(value)

        raise AttributeError(
            f"Device config {type(device_config).__name__} has no 'identifier', 'id', or 'device_id' attribute. "
            f"Override get_device_id() to specify which attribute to use."
        )

    def get_device_name(self, device_config: ConfigT) -> str:
        """
        Extract device name from configuration.

        Default implementation: tries common attribute names (name, friendly_name, device_name).
        Override this if your config uses a different attribute name.

        :param device_config: Device configuration
        :return: Device name
        :raises AttributeError: If no valid name attribute is found
        """
        for attr in ("name", "friendly_name", "device_name"):
            if hasattr(device_config, attr):
                value = getattr(device_config, attr)
                if value:
                    return str(value)

        raise AttributeError(
            f"Device config {type(device_config).__name__} has no 'name', 'friendly_name', or 'device_name' attribute. "
            f"Override get_device_name() to specify which attribute to use."
        )

    def get_additional_discovery_fields(self) -> list[dict]:
        """
        Get additional fields to show during discovery.

        Override to add custom fields (e.g., volume step, zone selection).

        :return: List of field definitions
        """
        return []

    def extract_additional_setup_data(
        self, input_values: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Extract additional setup data from input values.

        Override to extract additional custom fields.

        :param input_values: User input values
        :return: Dictionary of additional data
        """
        _ = input_values  # Mark as intentionally unused
        return {}

    async def get_pre_discovery_screen(self) -> RequestUserInput | None:
        """
        Request pre-discovery configuration screen(s).

        Override this method to show configuration screens BEFORE device discovery.
        This is useful for collecting credentials, API keys, server addresses, or
        other information needed to perform discovery.

        The collected data is stored in self._pre_discovery_data and can be accessed
        during discovery (in discover_devices()) or device creation.

        To show a pre-discovery screen:
        1. Return a RequestUserInput with the fields you need
        2. Handle the response in handle_pre_discovery_response()
        3. Return another RequestUserInput to show more screens, or None to proceed

        :return: RequestUserInput to show a screen, or None to skip pre-discovery
        """
        return None

    async def handle_pre_discovery_response(
        self, msg: UserDataResponse
    ) -> SetupAction:
        """
        Handle response from pre-discovery screens.

        Override this method to process responses from screens created by
        get_pre_discovery_screen(). The input values are automatically stored
        in self._pre_discovery_data before this method is called.

        You should:
        1. Validate the input (optionally)
        2. Either:
           - Return another RequestUserInput for more pre-discovery screens, or
           - Return None to proceed to device discovery

        If you return None, the base class will call discover_devices() where
        you can access self._pre_discovery_data to use the collected information.

        :param msg: User data response from pre-discovery screen
        :return: RequestUserInput for another screen, or None to proceed to discovery
        """
        _ = msg  # Mark as intentionally unused
        # Default: No additional handling, proceed to discovery
        return None

    async def get_additional_configuration_screen(
        self, device_config: ConfigT, previous_input: dict[str, Any]
    ) -> RequestUserInput | None:
        """
        Request additional configuration screens after device creation.

        Override this method to show additional setup screens that can modify
        the device configuration. This is called after create_device_from_manual_entry
        or create_device_from_discovery but BEFORE the device is saved.

        The device config is stored in self._pending_device_config and can be
        modified. To show another screen:

        1. Modify self._pending_device_config as needed
        2. Return a RequestUserInput for the next screen
        3. Handle the response in handle_additional_configuration_response()

        :param device_config: The device configuration (also in self._pending_device_config)
        :param previous_input: Input values from the previous screen
        :return: RequestUserInput to show another screen, or None to complete setup
        """
        _ = device_config  # Mark as intentionally unused
        _ = previous_input
        return None

    async def handle_additional_configuration_response(
        self, msg: UserDataResponse
    ) -> SetupAction:
        """
        Handle response from additional configuration screens.

        Override this method to process responses from custom setup screens
        created by get_additional_configuration_screen(). You should:

        1. Update self._pending_device_config with the user's input
        2. Either:
           - Return another RequestUserInput for more screens, or
           - Return None to trigger device save and SetupComplete

        If you return None, the base class will save self._pending_device_config
        and complete the setup.

        :param msg: User data response from additional screen
        :return: SetupAction (RequestUserInput for another screen, SetupComplete, or SetupError)
        """
        _ = msg  # Mark as intentionally unused
        # Default: No additional handling, save device and complete
        return None
