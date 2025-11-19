# Migration Guide: Converting to ucapi_base Framework

This guide helps you migrate an existing Unfolded Circle integration to use the ucapi_base framework. We'll show you the before/after for each component with real examples from the PSN integration migration.

## Table of Contents

- [Why Migrate?](#why-migrate)
- [Migration Overview](#migration-overview)
- [Step-by-Step Migration](#step-by-step-migration)
  - [1. Configuration Management](#1-configuration-management)
  - [2. Device Implementation](#2-device-implementation)
  - [3. Setup Flow](#3-setup-flow)
  - [4. Driver Integration](#4-driver-integration)
  - [5. Entity Implementation](#5-entity-implementation)
- [Common Patterns](#common-patterns)
- [Testing Your Migration](#testing-your-migration)

## Why Migrate?

**Before ucapi_base:**
- ~1500 lines of boilerplate per integration
- Manual configuration management with dict manipulation
- Global state management with module-level variables
- Repetitive event handler wiring
- Copy-paste setup flow code
- Manual device lifecycle management

**After ucapi_base:**
- ~400 lines of integration-specific code
- Type-safe configuration with dataclasses
- Clean OOP design with proper encapsulation
- Automatic event handler wiring
- Reusable setup flow base class
- Automatic device lifecycle management
- Full IDE autocomplete support

**Code Reduction:** ~70% less code to write and maintain!

## Migration Overview

The migration follows these steps:

1. **Configuration** - Replace dict-based config with typed dataclass + BaseDeviceManager
2. **Device** - Inherit from device interface (StatelessHTTPDevice, PollingDevice, etc.)
3. **Setup Flow** - Inherit from BaseSetupFlow, implement required methods
4. **Driver** - Inherit from BaseIntegrationDriver, remove global state
5. **Entities** - Update to reference device instances instead of global state

## Step-by-Step Migration

### 1. Configuration Management

#### Before: Dict-Based Configuration

```python
# config.py - Old approach
import json
import os
from typing import TypedDict

class PSNDevice(TypedDict):
    """PSN device configuration."""
    identifier: str
    name: str
    npsso: str

# Global configuration dict
devices: dict[str, PSNDevice] = {}
_config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

def _load() -> bool:
    """Load configuration from disk."""
    global devices
    if not os.path.exists(_config_path):
        return True
    
    try:
        with open(_config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            devices = {k: PSNDevice(**v) for k, v in data.items()}
        return True
    except Exception as e:
        return False

def _store() -> bool:
    """Store configuration to disk."""
    try:
        with open(_config_path, "w", encoding="utf-8") as f:
            json.dump({k: dict(v) for k, v in devices.items()}, f, indent=4)
        return True
    except Exception as e:
        return False

def add_device(device: PSNDevice) -> bool:
    """Add or update device."""
    devices[device["identifier"]] = device
    return _store()

def remove_device(identifier: str) -> bool:
    """Remove device."""
    if identifier in devices:
        devices.pop(identifier)
        return _store()
    return False

def get_device(identifier: str) -> PSNDevice | None:
    """Get device by identifier."""
    return devices.get(identifier)

def all_devices() -> list[PSNDevice]:
    """Get all configured devices."""
    return list(devices.values())

def clear() -> bool:
    """Clear all devices."""
    global devices
    devices = {}
    return _store()

# Initialize on import
_load()
```

**Problems:**
- ~80 lines of boilerplate
- Global mutable state
- Manual JSON serialization
- No type safety for operations
- Manual error handling everywhere
- Dict manipulation prone to errors

#### After: BaseDeviceManager with Dataclass

```python
# config.py - New approach
from dataclasses import dataclass
from ucapi_base import BaseDeviceManager

@dataclass
class PSNDevice:
    """PSN device configuration."""
    identifier: str
    name: str
    npsso: str

class PSNDeviceManager(BaseDeviceManager[PSNDevice]):
    """PSN device configuration manager with JSON persistence."""
    pass
```

**Benefits:**
- ~15 lines total (80% reduction!)
- No global state
- Type-safe operations
- Automatic JSON serialization
- Built-in error handling
- IDE autocomplete for all operations

**Usage Comparison:**

```python
# Old:
import config
device = config.get_device(device_id)
config.add_device(new_device)
all_devices = config.all_devices()

# New:
config = PSNDeviceManager("config.json", PSNDevice)
device = config.get(device_id)
config.add_or_update(new_device)
all_devices = config.all()
```

### 2. Device Implementation

#### Before: Manual Connection Management

```python
# psn.py - Old approach
class PSNAccount:
    """PlayStation Network account."""
    
    def __init__(self, identifier: str, name: str, npsso: str):
        self.identifier = identifier
        self.name = name
        self._npsso = npsso
        self.state = "OFF"
        self._ws = None
        self._ws_task = None
        self.events = EventEmitter()
        
    async def connect(self) -> bool:
        """Connect to PSN WebSocket."""
        try:
            # Manual WebSocket setup
            self._ws = await websockets.connect(
                "wss://psn-api.example.com/ws",
                extra_headers={"Authorization": f"Bearer {self._npsso}"}
            )
            
            # Manual task management
            self._ws_task = asyncio.create_task(self._receive_loop())
            
            self.events.emit("connected", self.identifier)
            return True
            
        except Exception as e:
            self.events.emit("connection_error", self.identifier, str(e))
            return False
    
    async def _receive_loop(self):
        """Manually manage WebSocket receive loop."""
        try:
            while self._ws:
                message = await self._ws.recv()
                data = json.loads(message)
                await self._process_message(data)
        except Exception as e:
            self.events.emit("connection_error", self.identifier, str(e))
        finally:
            await self.disconnect()
    
    async def disconnect(self) -> None:
        """Disconnect from WebSocket."""
        if self._ws_task:
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
            self._ws_task = None
            
        if self._ws:
            await self._ws.close()
            self._ws = None
            
        self.events.emit("disconnected", self.identifier)
    
    async def _process_message(self, data: dict) -> None:
        """Process received message."""
        self.state = data.get("state", "UNKNOWN")
        self.events.emit("state_changed", self.identifier, self.state)
```

**Problems:**
- ~100+ lines of connection boilerplate
- Manual WebSocket lifecycle management
- Manual task management and cancellation
- Error handling repeated everywhere
- Reconnection logic missing
- Testing difficult due to tight coupling

#### After: Inherit WebSocketDevice

```python
# psn.py - New approach
from ucapi_base import WebSocketDevice
import websockets
import json

class PSNAccount(WebSocketDevice):
    """PlayStation Network account using WebSocketDevice base."""
    
    def __init__(self, device_config):
        super().__init__(
            identifier=device_config.identifier,
            name=device_config.name
        )
        self._npsso = device_config.npsso
        self.state = "OFF"
    
    async def create_websocket(self):
        """Create WebSocket connection - called by base class."""
        return await websockets.connect(
            "wss://psn-api.example.com/ws",
            extra_headers={"Authorization": f"Bearer {self._npsso}"}
        )
    
    async def handle_message(self, message: str) -> None:
        """Handle received WebSocket message - called by base class."""
        data = json.loads(message)
        self.state = data.get("state", "UNKNOWN")
        self.events.emit("state_changed", self.identifier, self.state)
```

**Benefits:**
- ~30 lines (70% reduction!)
- Automatic WebSocket lifecycle management
- Automatic reconnection logic
- Automatic task management
- Built-in error handling and logging
- Easy to test with mocked WebSocket
- Focus on business logic only

**Other Device Patterns:**

```python
# For HTTP REST API devices:
class MyDevice(StatelessHTTPDevice):
    async def verify_connection(self) -> bool:
        """Test connection."""
        return await self._make_request("/status")
    
    async def handle_data_from_device(self, data: dict) -> None:
        """Process response."""
        pass

# For polling devices:
class MyDevice(PollingDevice):
    def __init__(self, config):
        super().__init__(
            identifier=config.device_id,
            name=config.name,
            poll_interval=5.0  # Poll every 5 seconds
        )
    
    async def poll_device(self) -> None:
        """Fetch and process state."""
        state = await self._fetch_state()
        self.events.emit("state_changed", self.identifier, state)
```

### 3. Setup Flow

#### Before: Manual Setup Flow Implementation

```python
# setup_flow.py - Old approach (~200 lines)
import config
from ucapi import SetupDriver, SetupError, SetupComplete

class PSNSetupFlow:
    """Manual setup flow implementation."""
    
    def __init__(self):
        self._setup_step = "START"
        self._pending_device = None
    
    async def handle_setup_request(self, msg):
        """Handle initial setup request."""
        if msg.reconfigure:
            return await self._show_configuration_mode()
        else:
            config.clear()
            return await self._show_manual_entry()
    
    async def handle_user_data_response(self, msg):
        """Route user responses to appropriate handlers."""
        if self._setup_step == "CONFIGURATION_MODE":
            return await self._handle_configuration_action(msg)
        elif self._setup_step == "MANUAL_ENTRY":
            return await self._handle_manual_entry_response(msg)
        # ... more manual routing
    
    async def _show_configuration_mode(self):
        """Show configuration mode screen."""
        self._setup_step = "CONFIGURATION_MODE"
        devices = config.all_devices()
        
        choices = [{"id": d["identifier"], "label": d["name"]} for d in devices]
        
        return RequestUserInput(
            title="PSN Configuration",
            settings=[
                {
                    "id": "choice",
                    "label": "Configured Devices",
                    "field": {"dropdown": {"items": choices}}
                },
                {
                    "id": "action",
                    "label": "Action",
                    "field": {"dropdown": {"items": [
                        {"id": "add", "label": "Add Device"},
                        {"id": "remove", "label": "Remove Device"},
                    ]}}
                }
            ]
        )
    
    async def _handle_configuration_action(self, msg):
        """Handle configuration mode actions."""
        action = msg.input_values.get("action")
        choice = msg.input_values.get("choice")
        
        if action == "add":
            return await self._show_manual_entry()
        elif action == "remove":
            if config.remove_device(choice):
                return SetupComplete()
            return SetupError()
        # ... more manual action handling
    
    async def _show_manual_entry(self):
        """Show manual entry form."""
        self._setup_step = "MANUAL_ENTRY"
        return RequestUserInput(
            title="Add PSN Account",
            settings=[
                {"id": "name", "label": "Name", "field": {"text": {"value": ""}}},
                {"id": "npsso", "label": "NPSSO Token", "field": {"text": {"value": ""}}},
            ]
        )
    
    async def _handle_manual_entry_response(self, msg):
        """Handle manual entry response."""
        name = msg.input_values["name"]
        npsso = msg.input_values["npsso"]
        
        # Create device config
        device_config = {
            "identifier": npsso[:8],  # Use part of token as ID
            "name": name,
            "npsso": npsso,
        }
        
        # Check for duplicates
        if config.get_device(device_config["identifier"]):
            return SetupError(error_type=IntegrationSetupError.DEVICE_EXISTS)
        
        # Save configuration
        if not config.add_device(device_config):
            return SetupError()
        
        return SetupComplete()
    
    # ... more methods for backup/restore, etc.
```

**Problems:**
- ~200+ lines of repetitive code
- Manual state management (`_setup_step`)
- Manual routing logic
- Duplicate device checking repeated
- Manual configuration screen building
- No reusability across integrations

#### After: Inherit BaseSetupFlow

```python
# setup_flow.py - New approach
from ucapi_base import BaseSetupFlow
from ucapi import IntegrationSetupError
import config

class PSNSetupFlow(BaseSetupFlow[config.PSNDevice]):
    """PSN setup flow using BaseSetupFlow."""
    
    async def discover_devices(self) -> list:
        """PSN doesn't support auto-discovery."""
        return []
    
    def get_manual_entry_fields(self) -> list[dict]:
        """Define manual entry fields."""
        return [
            {
                "id": "name",
                "label": {"en": "Account Name"},
                "field": {"text": {"value": ""}},
            },
            {
                "id": "npsso",
                "label": {"en": "NPSSO Token"},
                "field": {"text": {"value": ""}},
            },
        ]
    
    def create_device_from_manual_entry(
        self, input_values: dict[str, str]
    ) -> config.PSNDevice:
        """Create device config from manual entry."""
        name = input_values["name"]
        npsso = input_values["npsso"]
        
        return config.PSNDevice(
            identifier=npsso[:8],  # Use part of token as ID
            name=name,
            npsso=npsso,
        )
    
    def create_device_from_discovery(
        self, device_id: str, discovery_data: dict
    ) -> config.PSNDevice:
        """Not used - PSN doesn't support discovery."""
        raise NotImplementedError()
    
    def get_device_name(self, device_config: config.PSNDevice) -> str:
        """Extract device name."""
        return device_config.name
```

**Benefits:**
- ~50 lines (75% reduction!)
- No manual state management
- No manual routing
- Automatic duplicate checking
- Automatic configuration mode
- Built-in backup/restore
- Fully reusable pattern

**Features You Get For Free:**
- Configuration mode (add/update/remove/reset devices)
- Duplicate device detection
- Backup creation and restore
- Multi-screen setup flows
- Error handling and validation
- State management

### 4. Driver Integration

#### Before: Global State and Manual Event Handlers

```python
# driver.py - Old approach (~300 lines)
import asyncio
import ucapi
import ucapi.api as uc
from psn import PSNAccount
import config

_LOG = logging.getLogger("driver")
_LOOP = asyncio.get_event_loop()

# Global API and device storage
api = uc.IntegrationAPI(_LOOP)
_configured_accounts: dict[str, PSNAccount] = {}

@api.listens_to(ucapi.Events.CONNECT)
async def on_r2_connect_cmd() -> None:
    """Manually connect all devices."""
    _LOG.debug("Connect command")
    await api.set_device_state(ucapi.DeviceStates.CONNECTED)
    for account in _configured_accounts.values():
        await account.connect()

@api.listens_to(ucapi.Events.DISCONNECT)
async def on_r2_disconnect_cmd():
    """Manually disconnect all devices."""
    _LOG.debug("Disconnect command")
    for account in _configured_accounts.values():
        await account.disconnect()

@api.listens_to(ucapi.Events.ENTER_STANDBY)
async def on_r2_enter_standby() -> None:
    """Manually handle standby."""
    _LOG.debug("Enter standby")
    for account in _configured_accounts.values():
        await account.disconnect()

@api.listens_to(ucapi.Events.SUBSCRIBE_ENTITIES)
async def on_subscribe_entities(entity_ids: list[str]) -> None:
    """Manually subscribe to entities."""
    _LOG.debug("Subscribe: %s", entity_ids)
    for entity_id in entity_ids:
        account_id = entity_id  # entity_id IS account_id for PSN
        
        # Check if already configured
        if account_id in _configured_accounts:
            account = _configured_accounts[account_id]
            state = _map_psn_state(account.state)
            api.configured_entities.update_attributes(
                entity_id, {media_player.Attributes.STATE: state}
            )
            continue
        
        # Load from config
        device_config = config.get_device(account_id)
        if device_config:
            _add_configured_account(device_config)

def _add_configured_account(device_config: dict) -> None:
    """Manually create and wire up account."""
    account = PSNAccount(
        identifier=device_config["identifier"],
        name=device_config["name"],
        npsso=device_config["npsso"],
    )
    
    # Manual event handler setup
    account.events.on("connected", _on_account_connected)
    account.events.on("disconnected", _on_account_disconnected)
    account.events.on("connection_error", _on_account_error)
    account.events.on("state_changed", _on_state_changed)
    
    _configured_accounts[account.identifier] = account
    
    # Manual entity creation
    entity = _create_media_player_entity(account)
    api.available_entities.add(entity)

def _on_state_changed(account_id: str, state: str) -> None:
    """Manually update entity state."""
    mapped_state = _map_psn_state(state)
    api.configured_entities.update_attributes(
        account_id, {media_player.Attributes.STATE: mapped_state}
    )

def _map_psn_state(psn_state: str) -> media_player.States:
    """Manually map states."""
    match psn_state:
        case "PLAYING":
            return media_player.States.PLAYING
        case "ON" | "MENU":
            return media_player.States.ON
        case "OFF":
            return media_player.States.OFF
        case _:
            return media_player.States.UNKNOWN

# ... more manual setup
```

**Problems:**
- ~300 lines of boilerplate
- Global mutable state (`_configured_accounts`)
- Manual event handler registration
- Manual entity creation and registration
- Manual state synchronization
- Manual lifecycle management
- Difficult to test

#### After: Inherit BaseIntegrationDriver

```python
# driver.py - New approach
import asyncio
import logging
from typing import Any
from ucapi import media_player
from ucapi_base import BaseIntegrationDriver
import config
from config import PSNDevice
from psn import PSNAccount
from media_player import PSNMediaPlayer
from setup_flow import PSNSetupFlow

_LOG = logging.getLogger("driver")
_LOOP = asyncio.get_event_loop()

class PSNIntegrationDriver(BaseIntegrationDriver[PSNAccount, PSNDevice]):
    """PSN Integration driver."""
    
    def __init__(self, loop: asyncio.AbstractEventLoop):
        super().__init__(
            loop=loop,
            device_class=PSNAccount,
            entity_classes=[PSNMediaPlayer]
        )
    
    # ========================================================================
    # Required Methods - Integration-Specific Logic
    # ========================================================================
    
    def device_from_entity_id(self, entity_id: str) -> str | None:
        """Extract device ID from entity ID."""
        return entity_id  # For PSN, entity_id IS the device_id
    
    def get_entity_ids_for_device(self, device_id: str) -> list[str]:
        """Get entity IDs for a device."""
        return [device_id]  # One media_player per account
    
    def map_device_state(self, device_state: Any) -> media_player.States:
        """Map PSN state to media player state."""
        match device_state:
            case "PLAYING":
                return media_player.States.PLAYING
            case "ON" | "MENU":
                return media_player.States.ON
            case "OFF":
                return media_player.States.OFF
            case _:
                return media_player.States.UNKNOWN
    
    def create_entities(
        self, device_config: PSNDevice, device: PSNAccount
    ) -> list[PSNMediaPlayer]:
        """Create entity instances for a device."""
        return [PSNMediaPlayer(device_config, device)]

# Create driver instance
driver = PSNIntegrationDriver(_LOOP)
driver.register_setup_handler(PSNSetupFlow, config.PSNDeviceManager)
```

**Benefits:**
- ~90 lines (70% reduction!)
- No global state
- Automatic event handler registration
- Automatic entity lifecycle
- Automatic state synchronization
- Clean, testable design
- Focus on integration-specific logic only

**What You Get For Free:**
- Device lifecycle management
- Event handler wiring
- Entity registration
- State synchronization
- Remote Two event handling
- Configuration loading
- Error handling and logging

### 5. Entity Implementation

#### Before: Global References

```python
# media_player.py - Old approach
import ucapi
from ucapi import MediaPlayer

async def create_media_player_entity(account_id: str, name: str) -> MediaPlayer:
    """Create media player entity - referenced global state."""
    entity = MediaPlayer(
        identifier=account_id,
        name=ucapi.EntityName(name, "en"),
        features=[],
        attributes={},
        device_class=ucapi.media_player.DeviceClasses.TV,
    )
    return entity

# Command handler referenced global _configured_accounts dict
async def media_player_cmd_handler(entity, cmd_id, params):
    """Handler that needs global state."""
    import driver  # Circular import!
    account = driver._configured_accounts.get(entity.id)
    if not account:
        return ucapi.StatusCodes.NOT_FOUND
    # Handle command...
```

**Problems:**
- Circular dependencies
- Global state references
- No type safety
- Difficult to test
- Tight coupling

#### After: Instance References

```python
# media_player.py - New approach
import logging
from typing import Any
from ucapi import EntityName, MediaPlayer, StatusCodes, media_player
from config import PSNDevice
from psn import PSNAccount

_LOG = logging.getLogger(__name__)

class PSNMediaPlayer(MediaPlayer):
    """PSN Media Player entity with device reference."""
    
    def __init__(self, device_config: PSNDevice, device: PSNAccount):
        """Initialize with device instance - no global state."""
        self._device = device
        
        super().__init__(
            identifier=device_config.identifier,
            name=EntityName(device_config.name, "en"),
            features=[
                media_player.Features.ON_OFF,
                media_player.Features.TOGGLE,
            ],
            attributes={
                media_player.Attributes.STATE: media_player.States.UNKNOWN,
            },
            device_class=media_player.DeviceClasses.STREAMING_BOX,
            cmd_handler=self.handle_command,
        )
    
    async def handle_command(
        self, entity: MediaPlayer, cmd_id: str, params: dict[str, Any] | None
    ) -> StatusCodes:
        """Handle media player commands - uses self._device."""
        _LOG.info("Command: %s %s", cmd_id, params)
        
        # Direct device reference - no global lookup!
        if cmd_id == media_player.Commands.ON:
            await self._device.turn_on()
            return StatusCodes.OK
        
        if cmd_id == media_player.Commands.OFF:
            await self._device.turn_off()
            return StatusCodes.OK
        
        return StatusCodes.NOT_IMPLEMENTED
```

**Benefits:**
- No circular dependencies
- No global state
- Type-safe device reference
- Easy to test
- Clean separation of concerns

## Common Patterns

### Pattern: Multi-Device Integration

If your integration manages multiple device types:

```python
class MyDriver(BaseIntegrationDriver[MyDevice, MyDeviceConfig]):
    def get_entity_ids_for_device(self, device_id: str) -> list[str]:
        """Multiple entities per device."""
        return [
            f"{device_id}_player",
            f"{device_id}_light",
            f"{device_id}_sensor",
        ]
    
    def create_entities(self, device_config, device):
        """Create multiple entity types."""
        return [
            MyMediaPlayerEntity(device_config, device),
            MyLightEntity(device_config, device),
            MySensorEntity(device_config, device),
        ]
```

### Pattern: API Authentication

Use pre-discovery screens to collect credentials:

```python
class MySetupFlow(BaseSetupFlow[MyDeviceConfig]):
    async def get_pre_discovery_screen(self):
        """Collect API credentials before discovery."""
        return RequestUserInput(
            title="API Configuration",
            settings=[
                {"id": "api_key", "label": "API Key", "field": {"text": {...}}},
            ]
        )
    
    async def discover_devices(self):
        """Use credentials from self._pre_discovery_data."""
        api_key = self._pre_discovery_data.get("api_key")
        # Perform authenticated discovery...
```

### Pattern: Complex Setup

Use post-selection screens for additional configuration:

```python
async def get_additional_configuration_screen(self, device_config, previous_input):
    """Show zone selection after device chosen."""
    return RequestUserInput(
        title="Zone Configuration",
        settings=[
            {"id": "zone", "label": "Zone", "field": {"dropdown": {...}}},
        ]
    )

async def handle_additional_configuration_response(self, msg):
    """Update device config with zone."""
    self._pending_device_config.zone = msg.input_values["zone"]
    return None  # Complete setup
```

## Testing Your Migration

### Unit Testing

The new architecture is much easier to test:

```python
import pytest
from myintegration.driver import MyDriver
from myintegration.config import MyDeviceConfig

@pytest.fixture
def driver():
    loop = asyncio.get_event_loop()
    return MyDriver(loop)

@pytest.fixture
def device_config():
    return MyDeviceConfig(
        device_id="test123",
        name="Test Device",
        host="192.168.1.100",
    )

async def test_device_creation(driver, device_config):
    """Test device lifecycle without global state."""
    device = driver._device_class(device_config)
    assert device.identifier == "test123"
    assert device.name == "Test Device"

async def test_state_mapping(driver):
    """Test state mapping."""
    assert driver.map_device_state("PLAYING") == media_player.States.PLAYING
    assert driver.map_device_state("OFF") == media_player.States.OFF
```

### Integration Testing

Test with real Remote Two connection:

1. Run your integration
2. Add device through Remote Two UI
3. Verify device appears in `config.json`
4. Verify entity shows up in Remote Two
5. Test commands through Remote Two UI

### Migration Checklist

- [ ] Configuration converted to dataclass + BaseDeviceManager
- [ ] Device inherits from appropriate base class (StatelessHTTPDevice, PollingDevice, etc.)
- [ ] Setup flow inherits from BaseSetupFlow
- [ ] Driver inherits from BaseIntegrationDriver
- [ ] All global state removed
- [ ] Entities reference device instances, not globals
- [ ] Abstract method names updated (no underscores)
- [ ] Manual event handler registration removed
- [ ] Manual entity lifecycle code removed
- [ ] Unit tests updated
- [ ] Integration tests pass
- [ ] Documentation updated

## Need Help?

- Check the PSN integration in this repo for a complete example
- Review inline docstrings in ucapi_base modules
- See README.md for detailed API documentation
- Open an issue on GitHub for questions
