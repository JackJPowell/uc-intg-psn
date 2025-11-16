"""
Base integration driver for Unfolded Circle Remote integrations.

Provides common event handlers and device lifecycle management.

:copyright: (c) 2025 by Jack Powell.
:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Any, Generic, TypeVar

import ucapi
import ucapi.api as uc
from ucapi import EntityTypes, media_player
from .device import BaseDeviceInterface, DeviceEvents

# Type variables for generic device and entity types
DeviceT = TypeVar("DeviceT", bound=BaseDeviceInterface)  # Device interface type
ConfigT = TypeVar("ConfigT")  # Device configuration type (any object with attributes)

_LOG = logging.getLogger(__name__)


class BaseIntegrationDriver(ABC, Generic[DeviceT, ConfigT]):
    """
    Base class for Remote Two integration drivers.

    Handles common patterns like:
    - Event listeners (connect, disconnect, standby, subscribe/unsubscribe)
    - Device lifecycle management
    - Entity registration and updates
    - State propagation from devices to entities

    Type Parameters:
        DeviceT: The device interface class (e.g., YamahaAVR)
        ConfigT: The device configuration class (e.g., YamahaDevice)
    """

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        device_class: type[DeviceT],
        entity_classes: list[type],
    ):
        """
        Initialize the integration driver.

        :param loop: The asyncio event loop
        :param device_class: The device interface class to instantiate
        :param entity_classes: List of entity classes to create (e.g., [MediaPlayer, Remote])
        """
        self.api = uc.IntegrationAPI(loop)
        self._loop = loop
        self._device_class = device_class
        self._entity_classes = entity_classes
        self._configured_devices: dict[str, DeviceT] = {}
        self.config = None  # Will be set by integration after initialization
        self._setup_event_handlers()

    def _setup_event_handlers(self) -> None:
        """Register all event handlers with the API."""
        self.api.listens_to(ucapi.Events.CONNECT)(self._on_r2_connect_cmd)
        self.api.listens_to(ucapi.Events.DISCONNECT)(self._on_r2_disconnect_cmd)
        self.api.listens_to(ucapi.Events.ENTER_STANDBY)(self._on_r2_enter_standby)
        self.api.listens_to(ucapi.Events.EXIT_STANDBY)(self._on_r2_exit_standby)
        self.api.listens_to(ucapi.Events.SUBSCRIBE_ENTITIES)(
            self._on_subscribe_entities
        )
        self.api.listens_to(ucapi.Events.UNSUBSCRIBE_ENTITIES)(
            self._on_unsubscribe_entities
        )

    # ========================================================================
    # Event Handlers (Common implementation)
    # ========================================================================

    async def _on_r2_connect_cmd(self) -> None:
        """Connect all configured devices when Remote Two sends connect command."""
        _LOG.debug("Client connect command: connecting device(s)")
        await self.api.set_device_state(ucapi.DeviceStates.CONNECTED)
        for device in self._configured_devices.values():
            await device.connect()

    async def _on_r2_disconnect_cmd(self) -> None:
        """Disconnect all configured devices when Remote Two sends disconnect command."""
        _LOG.debug("Client disconnect command: disconnecting device(s)")
        for device in self._configured_devices.values():
            await device.disconnect()

    async def _on_r2_enter_standby(self) -> None:
        """Handle Remote Two entering standby mode."""
        _LOG.debug("Enter standby event: disconnecting device(s)")
        for device in self._configured_devices.values():
            await device.disconnect()

    async def _on_r2_exit_standby(self) -> None:
        """Handle Remote Two exiting standby mode."""
        _LOG.debug("Exit standby event: connecting device(s)")
        for device in self._configured_devices.values():
            await device.connect()

    async def _on_subscribe_entities(self, entity_ids: list[str]) -> None:
        """
        Subscribe to given entities.

        :param entity_ids: entity identifiers.
        """
        _LOG.debug("Subscribe entities event: %s", entity_ids)

        for entity_id in entity_ids:
            device_id = self.device_from_entity_id(entity_id)
            if device_id is None:
                continue

            # Check if device is already configured
            if device_id in self._configured_devices:
                device = self._configured_devices[device_id]
                _LOG.info("Entity '%s' subscribing to existing device", entity_id)
                _LOG.debug("Device State: %s", device.state)

                # Update entity state
                if device.state is None:
                    state = media_player.States.UNAVAILABLE
                else:
                    state = self.map_device_state(device.state)

                self.api.configured_entities.update_attributes(
                    entity_id, {media_player.Attributes.STATE: state}
                )
                continue

            # Device not configured yet, add it
            device_config = self.get_device_config(device_id)
            if device_config:
                # Add without connecting - connection will be handled by CONNECT event
                self.add_configured_device(device_config, connect=False)
            else:
                _LOG.error(
                    "Failed to subscribe entity %s: no device config found", entity_id
                )

    async def _on_unsubscribe_entities(self, entity_ids: list[str]) -> None:
        """
        Handle entity unsubscribe events.

        Only disconnects and cleans up a device if ALL its entities are unsubscribed.
        """
        _LOG.debug("Unsubscribe entities event: %s", entity_ids)

        # Track which devices need to be checked
        devices_to_check = set()

        for entity_id in entity_ids:
            device_id = self.device_from_entity_id(entity_id)
            if device_id is not None and device_id in self._configured_devices:
                devices_to_check.add(device_id)

        # For each device, check if any of its entities are still configured
        for device_id in devices_to_check:
            device_entities = self.get_entity_ids_for_device(device_id)
            any_entity_configured = any(
                self.api.configured_entities.get(entity_id) is not None
                for entity_id in device_entities
            )

            if not any_entity_configured:
                # No entities are configured anymore, disconnect and cleanup
                device = self._configured_devices.get(device_id)
                if device:
                    _LOG.info(
                        "No entities configured for device '%s', disconnecting and cleaning up",
                        device_id,
                    )
                    await device.disconnect()
                    device.events.remove_all_listeners()
                    self._configured_devices.pop(device_id, None)

    # ========================================================================
    # Device Lifecycle Management
    # ========================================================================

    def add_configured_device(
        self, device_config: ConfigT, connect: bool = True
    ) -> None:
        """
        Add and configure a device.

        :param device_config: Device configuration
        :param connect: Whether to initiate connection immediately
        """
        device_id = self.get_device_id(device_config)

        if device_id in self._configured_devices:
            _LOG.debug(
                "Device %s already configured, updating existing instance", device_id
            )
            device = self._configured_devices[device_id]
        else:
            _LOG.info(
                "Adding new device: %s (%s)",
                device_id,
                self.get_device_name(device_config),
            )
            device = self._device_class(device_config, loop=self._loop)
            self.setup_device_event_handlers(device)
            self._configured_devices[device_id] = device

        if connect:
            _LOG.debug("[%s] Creating connection task", device_id)
            self._loop.create_task(self._safe_connect(device))

        self.register_available_entities(device_config, device)

    async def _safe_connect(self, device: DeviceT) -> None:
        """Safely connect to device with error handling."""
        try:
            await device.connect()
        except Exception as err:  # pylint: disable=broad-exception-caught
            _LOG.error("Failed to connect to device: %s", err)

    def setup_device_event_handlers(self, device: DeviceT) -> None:
        """
        Attach event handlers to device.
        
        Override this method to add custom event handlers. Call super() first
        to register the default handlers, then add your custom ones.
        
        :param device: Device instance
        """
        device.events.on(DeviceEvents.CONNECTED, self.on_device_connected)
        device.events.on(DeviceEvents.DISCONNECTED, self.on_device_disconnected)
        device.events.on(DeviceEvents.ERROR, self.on_device_connection_error)
        device.events.on(DeviceEvents.UPDATE, self.on_device_update)

    def register_available_entities(
        self, device_config: ConfigT, device: DeviceT
    ) -> None:
        """
        Register available entities for a device.
        
        Override this method to customize entity registration logic. Call super()
        to use the default implementation that calls create_entities().

        :param device_config: Device configuration
        :param device: Device instance
        """
        device_id = self.get_device_id(device_config)
        _LOG.info("Registering available entities for %s", device_id)

        entities = self.create_entities(device_config, device)

        for entity in entities:
            if self.api.available_entities.contains(entity.id):
                self.api.available_entities.remove(entity.id)
            self.api.available_entities.add(entity)

    # ========================================================================
    # Device Event Handlers (can be overridden)
    # ========================================================================

    async def on_device_connected(self, device_id: str) -> None:
        """
        Handle device connection.

        :param device_id: Device identifier
        """
        _LOG.debug("Device connected: %s", device_id)

        if device_id not in self._configured_devices:
            _LOG.warning("Device %s is not configured", device_id)
            return

        device = self._configured_devices[device_id]
        state = (
            self.map_device_state(device.state)
            if device.state
            else media_player.States.UNKNOWN
        )

        for entity_id in self.get_entity_ids_for_device(device_id):
            configured_entity = self.api.configured_entities.get(entity_id)
            if configured_entity is None:
                _LOG.debug("Entity %s is not configured, ignoring", entity_id)
                continue

            if configured_entity.entity_type == EntityTypes.MEDIA_PLAYER:
                self.api.configured_entities.update_attributes(
                    entity_id, {media_player.Attributes.STATE: state}
                )
            elif configured_entity.entity_type == EntityTypes.REMOTE:
                self.api.configured_entities.update_attributes(
                    entity_id, {ucapi.remote.Attributes.STATE: state}
                )

        await self.api.set_device_state(ucapi.DeviceStates.CONNECTED)

    async def on_device_disconnected(self, device_id: str) -> None:
        """
        Handle device disconnection.

        :param device_id: Device identifier
        """
        _LOG.debug("Device disconnected: %s", device_id)

        for entity_id in self.get_entity_ids_for_device(device_id):
            configured_entity = self.api.configured_entities.get(entity_id)
            if configured_entity is None:
                continue

            if configured_entity.entity_type == EntityTypes.MEDIA_PLAYER:
                self.api.configured_entities.update_attributes(
                    entity_id,
                    {media_player.Attributes.STATE: media_player.States.UNAVAILABLE},
                )
            elif configured_entity.entity_type == EntityTypes.REMOTE:
                self.api.configured_entities.update_attributes(
                    entity_id,
                    {ucapi.remote.Attributes.STATE: ucapi.remote.States.UNAVAILABLE},
                )

    async def on_device_connection_error(self, device_id: str, message: str) -> None:
        """
        Handle device connection error.

        :param device_id: Device identifier
        :param message: Error message
        """
        _LOG.error("[%s] Connection error: %s", device_id, message)

        for entity_id in self.get_entity_ids_for_device(device_id):
            configured_entity = self.api.configured_entities.get(entity_id)
            if configured_entity is None:
                continue

            if configured_entity.entity_type == EntityTypes.MEDIA_PLAYER:
                self.api.configured_entities.update_attributes(
                    entity_id,
                    {media_player.Attributes.STATE: media_player.States.UNAVAILABLE},
                )
            elif configured_entity.entity_type == EntityTypes.REMOTE:
                self.api.configured_entities.update_attributes(
                    entity_id,
                    {ucapi.remote.Attributes.STATE: ucapi.remote.States.UNAVAILABLE},
                )

        await self.api.set_device_state(ucapi.DeviceStates.ERROR)

    async def on_device_update(
        self, device_id: str, update: dict[str, Any] | None
    ) -> None:
        """
        Handle device state updates.

        Override this method to customize update handling for your integration.

        :param device_id: Device identifier
        :param update: Dictionary containing updated properties
        """
        if update is None:
            _LOG.warning("[%s] Received None update, skipping", device_id)
            return

        # Default implementation - integrations should override for specific behavior
        _LOG.debug("[%s] Device update: %s", device_id, update)

    # ========================================================================
    # Abstract Methods (Must be implemented by subclasses)
    # ========================================================================

    @abstractmethod
    def device_from_entity_id(self, entity_id: str) -> str | None:
        """
        Extract device identifier from entity identifier.

        :param entity_id: Entity identifier (e.g., "media_player.device_123")
        :return: Device identifier or None
        """

    @abstractmethod
    def get_entity_ids_for_device(self, device_id: str) -> list[str]:
        """
        Get all entity identifiers for a device.

        :param device_id: Device identifier
        :return: List of entity identifiers
        """

    def get_device_config(self, device_id: str) -> ConfigT | None:
        """
        Get device configuration for the given device ID.

        Default implementation: checks _configured_devices first, then falls
        back to self.config.get() if config manager is available.
        Override this if your integration uses a different config structure.

        :param device_id: Device identifier
        :return: Device configuration or None
        """
        # First check if device is already configured
        device = self._configured_devices.get(device_id)
        if device:
            return device.device_config

        # Fall back to stored configuration if available
        if self.config and hasattr(self.config, "get"):
            return self.config.get(device_id)

        return None

    def get_device_id(self, device_config: ConfigT) -> str:
        """
        Extract device ID from device configuration.

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
        Extract device name from device configuration.

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

    def get_device_address(self, device_config: ConfigT) -> str:
        """
        Extract device address from device configuration.

        :param device_config: Device configuration
        :return: Device address
        """

        for attr in (
            "address",
            "host_address",
            "ip_address",
            "device_address",
            "host",
        ):
            if hasattr(device_config, attr):
                value = getattr(device_config, attr)
                if value:
                    return str(value)

        raise AttributeError(
            f"Device config {type(device_config).__name__} has no 'address', 'friendly_address', 'ip_address', 'device_address', or 'host' attribute. "
            f"Override get_device_address() to specify which attribute to use."
        )

    @abstractmethod
    def map_device_state(self, device_state: Any) -> media_player.States:
        """
        Map device-specific state to ucapi media player state.

        :param device_state: Device-specific state
        :return: Media player state
        """

    @abstractmethod
    def create_entities(self, device_config: ConfigT, device: DeviceT) -> list[Any]:
        """
        Create entity instances for a device.

        :param device_config: Device configuration
        :param device: Device instance
        :return: List of entity instances (MediaPlayer, Remote, etc.)
        """

    # ========================================================================
    # Utility Methods
    # ========================================================================

    def remove_device(self, device_id: str) -> None:
        """
        Remove a configured device.

        :param device_id: Device identifier
        """
        if device_id in self._configured_devices:
            _LOG.info("Removing device %s", device_id)
            device = self._configured_devices.pop(device_id)
            device.events.remove_all_listeners()

            # Remove all associated entities
            for entity_id in self.get_entity_ids_for_device(device_id):
                self.api.configured_entities.remove(entity_id)
                self.api.available_entities.remove(entity_id)
        else:
            _LOG.warning("Device %s not found in configured devices", device_id)

    def clear_devices(self) -> None:
        """Remove all configured devices."""
        _LOG.info("Clearing all configured devices")
        for device in self._configured_devices.values():
            device.events.remove_all_listeners()
        self._configured_devices.clear()
        self.api.configured_entities.clear()
        self.api.available_entities.clear()

    # ========================================================================
    # Configuration Change Callbacks (can be overridden)
    # ========================================================================

    def on_device_added(self, device_config: ConfigT) -> None:
        """
        Handle a newly added device in the configuration.

        Default implementation: Adds the device without connecting.
        Override if you need custom behavior.

        :param device_config: Device configuration that was added
        """
        _LOG.debug("Device added: %s", self.get_device_id(device_config))
        self.add_configured_device(device_config, connect=False)

    def on_device_removed(self, device_config: ConfigT | None) -> None:
        """
        Handle a removed device in the configuration.

        Default implementation: Removes the device or clears all if None.
        Override if you need custom behavior.

        :param device_config: Device configuration that was removed, or None to clear all
        """
        if device_config is None:
            _LOG.debug("Configuration cleared, removing all devices")
            self.clear_devices()
        else:
            device_id = self.get_device_id(device_config)
            _LOG.debug("Device removed: %s", device_id)
            self.remove_device(device_id)
