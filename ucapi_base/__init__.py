"""
Base classes and utilities for Unfolded Circle Remote integrations.

This module provides reusable base classes for integration drivers, setup flows,
device management, and device interfaces.

:copyright: (c) 2025 by Jack Powell.
:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

from .driver import BaseIntegrationDriver
from .setup import BaseSetupFlow, SetupSteps
from .config import BaseDeviceManager
from .device import (
    BaseDeviceInterface,
    StatelessHTTPDevice,
    PollingDevice,
    WebSocketDevice,
)
from .discovery import BaseDiscovery, DiscoveredDevice

__all__ = [
    "BaseIntegrationDriver",
    "BaseSetupFlow",
    "SetupSteps",
    "BaseDeviceManager",
    "BaseDeviceInterface",
    "StatelessHTTPDevice",
    "PollingDevice",
    "WebSocketDevice",
    "BaseDiscovery",
    "DiscoveredDevice",
]

__version__ = "0.1.0"
