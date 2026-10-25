# PlayStation Network Integration for Unfolded Circle Remote — Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## v3.0.0

### New entities

- **Remote controller**: navigate your PS4 or PS5 using directional controls, controller buttons and Home.
- **Power switch**: wake your console or put it into rest mode.
- **Profile selector**: choose between saved Remote Play pairings. Additional pairings can be registered through setup.
- **Console and connection sensors**: Remote Play Connection Status, PSN API Health, Installed Titles and Active Title ID for automations.
- **Trophy sensors**: Trophy Level, Earned Trophies, Platinum Trophies and Current Game Trophy Progress.

### New features

- **Installed games and apps**: browse the selected PS5's installed collection alongside your recently played games, with titles and artwork when available.
- **Launch games from the Remote**: start a PS5 title directly from the media browser using your PSN account.
- **Game details in the media browser**: open a game to view total playtime, play count, and first- and last-played dates.
- **Guided setup**: choose PSN with console controls, PSN only, or advanced local-only control. PSN setup includes sign-in and token retrieval links and retrieves your account ID automatically.
- **Multiple consoles**: add another PS5 using a saved PSN account, with separate controls and a game-launch selection for each console.
- **Console discovery and selection**: search your network, identify consoles by name, IP address and power state, or enter an IP manually.

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
