# PlayStation Network Integration for Unfolded Circle Remote — Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## Unreleased

### Added
- Maintained playdirector Remote Play engine, controller Remote entity, power switch and a profile selector.
- Active title ID for automations, connection/API health, installed-title count and four trophy sensors.
- Installed/recent game browsing, playtime details and confirmed Sony cloud game launching.
- Account-ID-only local setup, additional paired profiles and verified legacy-credential migration.

### Changed
- Keep entity state in the existing coordinator/subscription pattern, with observed LAN power independent of PSN availability.
- Serialize PSNAWP access, share cached public-library models, bound HTTP requests and validate authentication through real requests.
- Share the 300-request/15-minute PSN budget across configured devices and setup; cache inventory metadata for one hour.
- Avoid resetting the current-game trophy timer repeatedly while its refresh is pending.
- Preserve PSNAWP's limiter lifecycle when enforcing the shared request budget to avoid a background cleanup-thread race.
- Restore PSN sign-in/token links and separate the recommended setup from PSN-only and advanced local-only modes.
- Retrieve account IDs automatically, discover consoles by default, retry discovery across network interfaces, and select cloud consoles by name.
- Preserve the saved cloud console selection when refreshing PSN credentials.
- Distinguish discovered consoles by IP, power state and running game; show reported models and console names in cloud selection.
- Restore now-playing title, artwork and game platform for PS4 titles running on PS5; use matching cached metadata when LAN status omits artwork.
- Require playdirector 0.4.0

---

## v2.0.2 - 2026-07-12

### Fixed
- **Optional Remote Play setup**: setup now completes without starting console discovery or pairing when power control is not enabled
- **NPSSO token status latency**: the authentication sensor now reports a valid token immediately after authentication succeeds instead of waiting for the initial account-data poll

---

## v2.0.1 - 2026-07-11

### Added
- **NPSSO token status sensor**: reports whether the configured NPSSO token is valid, invalid, or still being checked

### Fixed
- **Preserve Remote Play configuration**: updating only the NPSSO token no longer removes existing Remote Play credentials when the power-control toggle is off
- **Local control with expired NPSSO tokens**: Remote Play credentials are loaded independently so local console commands remain available when PSN authentication expires

---

## v2.0.0 - 2026-04-24

### Added
- **PlayStation control**: Control the power state of PS4 and PS5 consoles directly from the Remote
- **PlayStation Discovery**: During setup your Playstation will be automatically discovered on your network
- **Power on / Power off**: Wake console from standby or send it into rest mode
- **PS5 home navigation**: `HOME` button command routes back to the PS5 home screen
- **Sensor entity** (`PSNSensor`): reports the currently running media title as a sensor value

### Fixed
- **Duplicate polling task race condition**: concurrent `CONNECT` and `SUBSCRIBE_ENTITIES` events could both call `connect()` before the poll task was recorded, spawning multiple background tasks

---

## v1.2.0 - 2026-04-11

### Added
- Media browser support: browse your played game library directly from the Remote UI, with cover art, game title, and last-played date for each title

### Changed
- Bumped `ucapi-framework` from `1.9.0` → `1.9.1`

---

## v1.1.7 - 2026-03-09

### Changes
Under the hood changes that will simply future development


---


## v0.1.0 - 2025-01-22

### Added
- First release. Control Yamaha clients on your local network from your Unfolded Circle Remote.
