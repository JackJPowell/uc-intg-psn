# UCAPI Base

A framework for building Unfolded Circle Remote integrations that handles the repetitive parts of integration development so you can focus on what's important.

## What This Solves

Building an Unfolded Circle Remote integration typically involves:
- Writing 200+ lines of setup flow routing logic
- Manually managing configuration updates and persistence
- Implementing device lifecycle management (connect/disconnect/reconnect)
- Wiring up Remote event handlers
- Managing global state for devices and entities
- Handling entity registration and state synchronization

This framework provides tested implementations of all these patterns, reducing a simple integration from ~1500 lines of boilerplate to ~400 lines of device-specific code. It even adds features, like back and restore, for free.

## Core Features

### Standard Setup Flow with Extension Points

The setup flow handles the common pattern: configuration mode → discovery/manual entry → device selection. But every integration has unique needs, so there are extension points at key moments:

- **Pre-discovery screens** - Collect API credentials or server addresses before running discovery
- **Post-selection screens** - Gather device-specific settings after the user picks a device
- **Custom discovery fields** - Add extra fields to the discovery screen (zones, profiles, etc.)

The framework handles all the routing, state management, duplicate checking, and configuration persistence. You just implement the screens you need.

**Reduction**: Setup flow code goes from ~200 lines to ~50 lines.

### Device Connection Patterns

Four base classes cover the common connection patterns:

**StatelessHTTPDevice** - For REST APIs. You implement `verify_connection()` to test reachability. No connection management needed.

**PollingDevice** - For devices that need periodic state checks. You set a poll interval and implement `poll_device()`. Automatic reconnection on errors.

**WebSocketDevice** - For WebSocket connections. You implement `create_websocket()` and `handle_message()`. Framework manages the connection lifecycle, reconnection, and cleanup.

**PersistentConnectionDevice** - For TCP, serial, or custom protocols. You implement `establish_connection()`, `receive_data()`, and `close_connection()`. Framework handles the receive loop and error recovery.

All connection management, error handling, reconnection logic, and cleanup happens automatically.

**Reduction**: Device implementation goes from ~100 lines of connection boilerplate to ~30 lines of business logic.

### Configuration Management

Configuration is just a dataclass. The framework handles JSON serialization, CRUD operations, and persistence:

```python
@dataclass
class MyDeviceConfig:
    device_id: str
    name: str
    host: str

config = BaseDeviceManager("config.json", MyDeviceConfig)
```

You get full CRUD operations: `add_or_update()`, `get()`, `remove()`, `all()`, `clear()`. Plus automatic backup/restore functionality for free. The framework handles all the file I/O, error handling, and atomic writes.

Full type safety means IDE autocomplete works everywhere. No more dict manipulation or manual JSON handling.

**Reduction**: Configuration management goes from ~80 lines to ~15 lines.

### Driver Integration

The driver coordinates everything - device lifecycle, entity management, and Remote events. You implement four required methods that define your integration's specifics:

- **`device_from_entity_id()`** - Extract device ID from entity ID
- **`get_entity_ids_for_device()`** - Map device to its entities
- **`map_device_state()`** - Convert device state to entity state
- **`create_entities()`** - Instantiate entity objects

Everything else is automatic. The framework handles Remote connection events (connect, disconnect, standby), entity subscriptions, device lifecycle management, and state synchronization. You can override the event handlers if needed, but the defaults work for most cases.

Device events (like state changes) automatically propagate to entity state updates. The framework maintains the connection between your devices and your remote.

**Reduction**: Driver code goes from ~300 lines to ~90 lines.

### Discovery (Optional)

If your devices support network discovery, the framework provides implementations for common protocols:

**SSDPDiscovery** - For UPnP/SSDP devices. Define your service type and implement `create_discovered_device()` to convert SSDP responses into device configs.

**ZeroconfDiscovery** - For mDNS/Bonjour devices. Same pattern: service type + conversion method.

**NetworkProbeDiscovery** - For devices that need active probing. Scans local network ranges and calls your `probe_host()` method for each IP.

All discovery classes handle the protocol details, timeouts, and error handling. Dependencies are lazy-loaded, so you only install what you use (ssdpy, zeroconf, etc.). If your integration doesn't support discovery, just return an empty list from `discover_devices()` and focus on manual entry.

### Event System

The driver base class automatically wires up Remote events (connect, disconnect, standby, subscribe/unsubscribe) with sensible defaults. You can override any of them, but the defaults handle most cases.

Device events (state changes, errors) automatically propagate to entity state updates. You just emit events from your device and the framework keeps the Remote in sync.

## How It Works

You inherit from base classes and implement a few required methods:

**Driver** - Map between device states and entity states. Create entity instances.

**Device** - Implement your connection pattern (verify, poll, handle messages, etc.).

**Setup Flow** - Define how to discover devices and create configurations from user input.

**Config** - Just a dataclass.

The framework handles everything else: lifecycle management, event routing, state synchronization, configuration persistence, error handling, and reconnection logic.

## Architecture

The framework is layered:

```
Your Integration (device logic, API calls, protocol handling)
         ↓
BaseIntegrationDriver (lifecycle, events, entity management)
         ↓
Device Interfaces (connection patterns, error handling)
         ↓
Setup Flow + Config Manager (user interaction, persistence)
```

Each layer handles its responsibility and provides clean extension points. You only touch the top layer.

## Generic Type System

The framework uses bounded generics (`DeviceT`, `ConfigT`) so your IDE knows exactly what types you're working with:

```python
class MyDriver(BaseIntegrationDriver[MyDevice, MyDeviceConfig]):
    def get_device(self, device_id: str) -> MyDevice | None:
        device = super().get_device(device_id)
        # IDE knows device is MyDevice, full autocomplete available
```

No casting, no generic types, just full type safety throughout.

## Discovery Support

Optional discovery implementations for common protocols:

- **SSDPDiscovery** - For UPnP/SSDP devices
- **ZeroconfDiscovery** - For mDNS/Bonjour devices  
- **NetworkProbeDiscovery** - For scanning IP ranges

Lazy imports mean you only need the dependencies if you use them.

## Real-World Example

See the PSN integration in this repository:

- `intg-psn/driver.py` - 90 lines (was 300)
- `intg-psn/psn.py` - 140 lines (was 240)
- `intg-psn/setup_flow.py` - 50 lines (was 250)
- `intg-psn/config.py` - 15 lines (was 95)

Total: ~295 lines of integration code vs ~885 lines previously. And the new code is type-safe, testable, and maintainable.

## Migration

If you have an existing integration, see [MIGRATION_GUIDE.md](MIGRATION_GUIDE.md) for step-by-step instructions with before/after examples.

## Requirements

- Python 3.11+
- ucapi
- pyee

Optional (only if you use them):
- aiohttp (for HTTP devices)
- websockets (for WebSocket devices)
- ssdpy (for SSDP discovery)
- zeroconf (for mDNS discovery)

## License

Mozilla Public License Version 2.0