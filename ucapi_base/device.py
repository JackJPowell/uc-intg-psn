"""
Base device interface classes for Unfolded Circle Remote integrations.

Provides base classes for different device connection patterns:
- Stateless HTTP devices
- Polling devices
- WebSocket devices
- Persistent connection devices

:copyright: (c) 2025 by Jack Powell.
:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import asyncio
import logging
from abc import ABC, abstractmethod
from asyncio import AbstractEventLoop
from enum import IntEnum
from typing import Any

import aiohttp
from pyee.asyncio import AsyncIOEventEmitter

_LOG = logging.getLogger(__name__)

BACKOFF_MAX = 30
BACKOFF_SEC = 2


class DeviceEvents(IntEnum):
    """Common device events."""

    CONNECTING = 0
    CONNECTED = 1
    DISCONNECTED = 2
    PAIRED = 3
    ERROR = 4
    UPDATE = 5


class BaseDeviceInterface(ABC):
    """
    Base class for all device interfaces.

    Provides common functionality:
    - Event emitter for device state changes
    - Connection lifecycle management
    - Property accessors for device information
    - Logging helpers
    """

    def __init__(self, device_config: Any, loop: AbstractEventLoop | None = None):
        """
        Create device interface instance.

        :param device_config: Device configuration
        :param loop: Event loop
        """
        self._loop: AbstractEventLoop = loop or asyncio.get_running_loop()
        self.events = AsyncIOEventEmitter(self._loop)
        self._device_config = device_config
        self._state: Any = None

    @property
    def device_config(self) -> Any:
        """Return the device configuration."""
        return self._device_config

    @property
    @abstractmethod
    def identifier(self) -> str:
        """Return the device identifier."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Return the device name."""

    @property
    @abstractmethod
    def address(self) -> str | None:
        """Return the device address."""

    @property
    @abstractmethod
    def log_id(self) -> str:
        """Return a log identifier for the device."""

    @property
    def state(self) -> Any:
        """Return the current device state."""
        return self._state

    @abstractmethod
    async def connect(self) -> None:
        """Establish connection to the device."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect from the device."""


class StatelessHTTPDevice(BaseDeviceInterface):
    """
    Base class for devices with stateless HTTP API.

    No persistent connection is maintained. Each command creates a new
    HTTP session for the request.

    Good for: REST APIs, simple HTTP devices without a persistent connection (e.g., websockets)
    """

    def __init__(self, device_config: Any, loop: AbstractEventLoop | None = None):
        """Initialize stateless HTTP device."""
        super().__init__(device_config, loop)
        self._is_connected = False
        self._session_timeout = aiohttp.ClientTimeout(total=10)

    async def connect(self) -> None:
        """
        Establish connection (verify device is reachable).

        For stateless devices, this typically means verifying the device
        responds to a basic request.
        """
        _LOG.debug("[%s] Connecting to device at %s", self.log_id, self.address)
        self.events.emit(DeviceEvents.CONNECTING, self.identifier)

        try:
            await self.verify_connection()
            self._is_connected = True
            self.events.emit(DeviceEvents.CONNECTED, self.identifier)
            _LOG.info("[%s] Connected", self.log_id)
        except Exception as err:  # pylint: disable=broad-exception-caught
            _LOG.error("[%s] Connection error: %s", self.log_id, err)
            self.events.emit(DeviceEvents.ERROR, self.identifier, str(err))
            self._is_connected = False

    async def disconnect(self) -> None:
        """Disconnect from device (mark as disconnected)."""
        _LOG.debug("[%s] Disconnecting from device", self.log_id)
        self._is_connected = False
        self.events.emit(DeviceEvents.DISCONNECTED, self.identifier)

    @abstractmethod
    async def verify_connection(self) -> None:
        """
        Verify the device connection.

        Should make a simple request to verify device is reachable.
        Raises exception if connection fails.
        """

    async def _http_request(
        self, method: str, url: str, **kwargs
    ) -> aiohttp.ClientResponse:
        """
        Make an HTTP request to the device.

        :param method: HTTP method (GET, POST, PUT, etc.)
        :param url: Full URL or path
        :param kwargs: Additional arguments for aiohttp request
        :return: HTTP response
        """
        async with aiohttp.ClientSession(timeout=self._session_timeout) as session:
            async with session.request(method, url, **kwargs) as response:
                response.raise_for_status()
                return response


class PollingDevice(BaseDeviceInterface):
    """
    Base class for devices requiring periodic status polling.

    Maintains a polling task that periodically queries the device for status updates.

    Good for: Devices without push notifications, devices with changing state
    """

    def __init__(
        self,
        device_config: Any,
        loop: AbstractEventLoop | None = None,
        poll_interval: int = 30,
    ):
        """
        Initialize polling device.

        :param device_config: Device configuration
        :param loop: Event loop
        :param poll_interval: Polling interval in seconds
        """
        super().__init__(device_config, loop)
        self._poll_interval = poll_interval
        self._poll_task: asyncio.Task | None = None
        self._stop_polling = asyncio.Event()

    async def connect(self) -> None:
        """Establish connection and start polling."""
        # Prevent multiple concurrent connections
        if self._poll_task and not self._poll_task.done():
            _LOG.debug(
                "[%s] Already connected and polling, skipping connect", self.log_id
            )
            return

        _LOG.debug("[%s] Connecting and starting poll", self.log_id)
        self.events.emit(DeviceEvents.CONNECTING, self.identifier)

        try:
            await self.establish_connection()
            self._stop_polling.clear()
            self._poll_task = asyncio.create_task(self._poll_loop())
            self.events.emit(DeviceEvents.CONNECTED, self.identifier)
            _LOG.info("[%s] Connected and polling started", self.log_id)
        except Exception as err:  # pylint: disable=broad-exception-caught
            _LOG.error("[%s] Connection error: %s", self.log_id, err)
            self.events.emit(DeviceEvents.ERROR, self.identifier, str(err))

    async def disconnect(self) -> None:
        """Stop polling and disconnect."""
        _LOG.debug("[%s] Disconnecting and stopping poll", self.log_id)
        self._stop_polling.set()

        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass

        self._poll_task = None
        self.events.emit(DeviceEvents.DISCONNECTED, self.identifier)

    async def _poll_loop(self) -> None:
        """Main polling loop."""
        _LOG.debug("[%s] Poll loop started", self.log_id)

        while not self._stop_polling.is_set():
            try:
                await self.poll_device()
            except asyncio.CancelledError:
                break
            except Exception as err:  # pylint: disable=broad-exception-caught
                _LOG.error("[%s] Poll error: %s", self.log_id, err)

            try:
                await asyncio.wait_for(
                    self._stop_polling.wait(), timeout=self._poll_interval
                )
            except asyncio.TimeoutError:
                pass  # Normal timeout, continue polling

        _LOG.debug("[%s] Poll loop stopped", self.log_id)

    @abstractmethod
    async def establish_connection(self) -> None:
        """
        Establish initial connection to device.

        Called once when connect() is invoked.
        """

    @abstractmethod
    async def poll_device(self) -> None:
        """
        Poll the device for status updates.

        Called periodically based on poll_interval.
        Should emit UPDATE events with changed state.
        """


class WebSocketDevice(BaseDeviceInterface):
    """
    Base class for devices with WebSocket connections.

    Maintains a persistent WebSocket connection and handles incoming messages.

    Good for: Devices with WebSocket APIs, real-time updates
    """

    def __init__(self, device_config: Any, loop: AbstractEventLoop | None = None):
        """Initialize WebSocket device."""
        super().__init__(device_config, loop)
        self._ws: Any = None
        self._ws_task: asyncio.Task | None = None
        self._stop_ws = asyncio.Event()

    async def connect(self) -> None:
        """Establish WebSocket connection."""
        _LOG.debug("[%s] Connecting WebSocket to %s", self.log_id, self.address)
        self.events.emit(DeviceEvents.CONNECTING, self.identifier)

        try:
            self._ws = await self.create_websocket()
            self._stop_ws.clear()
            self._ws_task = asyncio.create_task(self._message_loop())
            self.events.emit(DeviceEvents.CONNECTED, self.identifier)
            _LOG.info("[%s] WebSocket connected", self.log_id)
        except Exception as err:  # pylint: disable=broad-exception-caught
            _LOG.error("[%s] WebSocket connection error: %s", self.log_id, err)
            self.events.emit(DeviceEvents.ERROR, self.identifier, str(err))

    async def disconnect(self) -> None:
        """Close WebSocket connection."""
        _LOG.debug("[%s] Disconnecting WebSocket", self.log_id)
        self._stop_ws.set()

        if self._ws_task and not self._ws_task.done():
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass

        if self._ws:
            await self.close_websocket()
            self._ws = None

        self._ws_task = None
        self.events.emit(DeviceEvents.DISCONNECTED, self.identifier)

    async def _message_loop(self) -> None:
        """Main message loop for receiving WebSocket messages."""
        _LOG.debug("[%s] WebSocket message loop started", self.log_id)

        try:
            while not self._stop_ws.is_set():
                message = await self.receive_message()
                if message is None:
                    break
                await self.handle_message(message)
        except asyncio.CancelledError:
            pass
        except Exception as err:  # pylint: disable=broad-exception-caught
            _LOG.error("[%s] WebSocket error: %s", self.log_id, err)
            self.events.emit(DeviceEvents.ERROR, self.identifier, str(err))

        _LOG.debug("[%s] WebSocket message loop stopped", self.log_id)

    @abstractmethod
    async def create_websocket(self) -> Any:
        """
        Create and return WebSocket connection.

        :return: WebSocket connection object
        """

    @abstractmethod
    async def close_websocket(self) -> None:
        """Close the WebSocket connection."""

    @abstractmethod
    async def receive_message(self) -> Any:
        """
        Receive a message from WebSocket.

        :return: Message data or None if connection closed
        """

    @abstractmethod
    async def handle_message(self, message: Any) -> None:
        """
        Handle incoming WebSocket message.

        :param message: Message data
        """


class PersistentConnectionDevice(BaseDeviceInterface):
    """
    Base class for devices with persistent TCP/protocol connections.

    Maintains a persistent connection with reconnection logic and backoff.

    Good for: Proprietary protocols, TCP connections, devices requiring persistent sessions
    """

    def __init__(
        self,
        device_config: Any,
        loop: AbstractEventLoop | None = None,
        backoff_max: int = BACKOFF_MAX,
    ):
        """
        Initialize persistent connection device.

        :param device_config: Device configuration
        :param loop: Event loop
        :param backoff_max: Maximum backoff time in seconds
        """
        super().__init__(device_config, loop)
        self._connection: Any = None
        self._reconnect_task: asyncio.Task | None = None
        self._stop_reconnect = asyncio.Event()
        self._backoff_max = backoff_max
        self._backoff_current = BACKOFF_SEC

    async def connect(self) -> None:
        """Establish persistent connection with reconnection logic."""
        _LOG.debug("[%s] Starting persistent connection", self.log_id)
        self._stop_reconnect.clear()
        self._reconnect_task = asyncio.create_task(self._connection_loop())

    async def disconnect(self) -> None:
        """Close persistent connection."""
        _LOG.debug("[%s] Stopping persistent connection", self.log_id)
        self._stop_reconnect.set()

        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass

        if self._connection:
            await self.close_connection()
            self._connection = None

        self._reconnect_task = None
        self.events.emit(DeviceEvents.DISCONNECTED, self.identifier)

    async def _connection_loop(self) -> None:
        """Main connection loop with automatic reconnection."""
        while not self._stop_reconnect.is_set():
            try:
                _LOG.debug("[%s] Establishing connection", self.log_id)
                self.events.emit(DeviceEvents.CONNECTING, self.identifier)

                self._connection = await self.establish_connection()
                self._backoff_current = BACKOFF_SEC  # Reset backoff on success
                self.events.emit(DeviceEvents.CONNECTED, self.identifier)
                _LOG.info("[%s] Connected", self.log_id)

                # Maintain connection
                await self.maintain_connection()

            except asyncio.CancelledError:
                break
            except Exception as err:  # pylint: disable=broad-exception-caught
                _LOG.error("[%s] Connection error: %s", self.log_id, err)
                self.events.emit(DeviceEvents.ERROR, self.identifier, str(err))

                if self._connection:
                    await self.close_connection()
                    self._connection = None

                # Exponential backoff
                if not self._stop_reconnect.is_set():
                    _LOG.debug(
                        "[%s] Reconnecting in %d seconds",
                        self.log_id,
                        self._backoff_current,
                    )
                    try:
                        await asyncio.wait_for(
                            self._stop_reconnect.wait(), timeout=self._backoff_current
                        )
                    except asyncio.TimeoutError:
                        pass

                    self._backoff_current = min(
                        self._backoff_current * 2, self._backoff_max
                    )

    @abstractmethod
    async def establish_connection(self) -> Any:
        """
        Establish connection to device.

        :return: Connection object
        """

    @abstractmethod
    async def close_connection(self) -> None:
        """Close the connection."""

    @abstractmethod
    async def maintain_connection(self) -> None:
        """
        Maintain the connection.

        This method should block while the connection is active.
        Return when connection is lost or should be closed.
        """
