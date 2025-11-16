"""
Base discovery classes for Unfolded Circle Remote integrations.

Provides base classes and protocols for device discovery.

Optional Dependencies:
    - ssdpy: Required only if using SSDPDiscovery
    - zeroconf: Required only if using ZeroconfDiscovery

The imports are lazy-loaded, so integrations that don't use discovery
don't need to install these dependencies.

:copyright: (c) 2025 by Jack Powell.
:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable

_LOG = logging.getLogger(__name__)


@dataclass
class DiscoveredDevice:
    """
    Common structure for discovered devices.

    All discovery implementations should return this format.
    """

    identifier: str
    """Unique identifier for the device (e.g., MAC address, serial number)"""

    name: str
    """Human-readable device name"""

    address: str
    """Device address (IP address, Bluetooth address, etc.)"""

    extra_data: dict[str, Any] | None = None
    """Optional additional data specific to the discovery method"""

    def __repr__(self):
        return f"DiscoveredDevice(id={self.identifier}, name={self.name}, address={self.address})"


class BaseDiscovery(ABC):
    """
    Base class for device discovery.

    Provides a common interface for different discovery methods:
    - SSDP
    - mDNS/Bonjour
    - Bluetooth LE
    - Cloud API
    - Network scanning
    """

    def __init__(self, timeout: int = 5):
        """
        Initialize discovery.

        :param timeout: Discovery timeout in seconds
        """
        self.timeout = timeout
        self._discovered_devices: list[DiscoveredDevice] = []

    @abstractmethod
    async def discover(self) -> list[DiscoveredDevice]:
        """
        Perform device discovery.

        :return: List of discovered devices
        """

    def clear(self) -> None:
        """Clear the list of discovered devices."""
        self._discovered_devices.clear()


class SSDPDiscovery(BaseDiscovery):
    """
    SSDP-based device discovery.

    Uses Simple Service Discovery Protocol to find devices on the local network.
    Good for: UPnP devices, media renderers, smart TVs
    """

    def __init__(
        self,
        search_target: str = "ssdp:all",
        timeout: int = 5,
        device_filter: Callable | None = None,
    ):
        """
        Initialize SSDP discovery.

        :param search_target: SSDP search target (e.g., "ssdp:all", "urn:schemas-upnp-org:device:MediaRenderer:1")
        :param timeout: Discovery timeout in seconds
        :param device_filter: Optional filter function to filter discovered devices
        """
        super().__init__(timeout)
        self.search_target = search_target
        self.device_filter = device_filter

    async def discover(self) -> list[DiscoveredDevice]:
        """
        Perform SSDP discovery.

        :return: List of discovered devices
        """
        _LOG.info(
            "Starting SSDP discovery (target: %s, timeout: %ds)",
            self.search_target,
            self.timeout,
        )

        try:
            try:
                from ssdpy import SSDPClient  # type: ignore[import-not-found]
            except ImportError as err:
                raise ImportError(
                    "ssdpy package is required for SSDP discovery. "
                    "Install it with: pip install ssdpy"
                ) from err

            client = SSDPClient(timeout=self.timeout)
            raw_devices = client.m_search(self.search_target)

            _LOG.debug("Found %d SSDP devices", len(raw_devices))

            self._discovered_devices.clear()

            for raw_device in raw_devices:
                # Apply filter if provided
                if self.device_filter and not self.device_filter(raw_device):
                    continue

                # Parse device info
                device = self.parse_ssdp_device(raw_device)
                if device:
                    self._discovered_devices.append(device)

            _LOG.info(
                "SSDP discovery complete: found %d device(s)",
                len(self._discovered_devices),
            )

        except Exception as err:  # pylint: disable=broad-exception-caught
            _LOG.error("SSDP discovery error: %s", err)

        return self._discovered_devices

    @abstractmethod
    def parse_ssdp_device(self, raw_device: dict) -> DiscoveredDevice | None:
        """
        Parse raw SSDP device data into DiscoveredDevice.

        Override this method to extract device information from SSDP response.

        :param raw_device: Raw SSDP device data
        :return: DiscoveredDevice or None if parsing fails
        """


class MDNSDiscovery(BaseDiscovery):
    """
    mDNS/Bonjour-based device discovery.

    Uses multicast DNS to discover devices advertising services.
    Good for: Apple devices, HomeKit, Chromecast, many IoT devices
    """

    def __init__(
        self,
        service_type: str,
        timeout: int = 5,
    ):
        """
        Initialize mDNS discovery.

        :param service_type: mDNS service type (e.g., "_airplay._tcp.local.", "_googlecast._tcp.local.")
        :param timeout: Discovery timeout in seconds
        """
        super().__init__(timeout)
        self.service_type = service_type

    async def discover(self) -> list[DiscoveredDevice]:
        """
        Perform mDNS discovery.

        :return: List of discovered devices
        """
        _LOG.info(
            "Starting mDNS discovery (service: %s, timeout: %ds)",
            self.service_type,
            self.timeout,
        )

        try:
            try:
                from zeroconf import ServiceBrowser, Zeroconf  # type: ignore[import-not-found]
            except ImportError as err:
                raise ImportError(
                    "zeroconf package is required for mDNS discovery. "
                    "Install it with: pip install zeroconf"
                ) from err

            import asyncio

            zeroconf = Zeroconf()
            discovered = []

            class Listener:
                def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
                    info = zc.get_service_info(type_, name)
                    if info:
                        discovered.append(info)

                def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
                    pass

                def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
                    pass

            browser = ServiceBrowser(zeroconf, self.service_type, Listener())

            # Wait for discovery timeout
            await asyncio.sleep(self.timeout)

            browser.cancel()
            zeroconf.close()

            self._discovered_devices.clear()

            for service_info in discovered:
                device = self.parse_mdns_service(service_info)
                if device:
                    self._discovered_devices.append(device)

            _LOG.info(
                "mDNS discovery complete: found %d device(s)",
                len(self._discovered_devices),
            )

        except Exception as err:  # pylint: disable=broad-exception-caught
            _LOG.error("mDNS discovery error: %s", err)

        return self._discovered_devices

    @abstractmethod
    def parse_mdns_service(self, service_info: Any) -> DiscoveredDevice | None:
        """
        Parse mDNS service info into DiscoveredDevice.

        Override this method to extract device information from mDNS service.

        :param service_info: mDNS service info object
        :return: DiscoveredDevice or None if parsing fails
        """


class NetworkScanDiscovery(BaseDiscovery):
    """
    Network scanning-based device discovery.

    Scans IP range for devices responding on specific ports.
    Good for: Devices without standard discovery protocols
    """

    def __init__(
        self,
        ip_range: str,
        ports: list[int],
        timeout: int = 5,
    ):
        """
        Initialize network scan discovery.

        :param ip_range: IP range to scan (e.g., "192.168.1.0/24")
        :param ports: List of ports to check
        :param timeout: Discovery timeout in seconds
        """
        super().__init__(timeout)
        self.ip_range = ip_range
        self.ports = ports

    async def discover(self) -> list[DiscoveredDevice]:
        """
        Perform network scan discovery.

        :return: List of discovered devices
        """
        _LOG.info(
            "Starting network scan (range: %s, ports: %s, timeout: %ds)",
            self.ip_range,
            self.ports,
            self.timeout,
        )

        # Implementation would scan network and check ports
        # This is a placeholder - actual implementation depends on specific needs

        _LOG.warning("Network scan discovery not yet implemented")
        return []

    @abstractmethod
    async def probe_device(self, ip: str, port: int) -> DiscoveredDevice | None:
        """
        Probe a specific IP:port for device information.

        Override this method to implement device probing logic.

        :param ip: IP address to probe
        :param port: Port to probe
        :return: DiscoveredDevice or None if not a valid device
        """
