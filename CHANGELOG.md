# PlayStation Network Integration for Unfolded Circle Remote — Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## Unreleased

---

## v1.2.0 - 2026-04-11

### Added
- Media browser support: browse your played game library directly from the Remote UI, with cover art, game title, and last-played date for each title
- `get_game_library()` on `PSNAccount` fetches a paginated page of titles from the PSN API using the PSNAWP `title_stats` iterator, running synchronous I/O in an executor to avoid blocking the event loop

### Changed
- Migrated to the ucapi-framework coordinator pattern: device state is stored as typed fields on `PSNAccount` and propagated to entities via `push_update()` / `sync_state()`, replacing the previous `get_entity_by_id` + `entity.update()` approach
- `PSNMediaPlayer` now inherits from `MediaPlayerEntity` (ucapi-framework) instead of the `UCMediaPlayer + Entity` mixin, and overrides `sync_state()` to read state from the device
- Bumped `ucapi` from `0.5.2` → `0.6.0`
- Bumped `ucapi-framework` from `1.9.0` → `1.9.1`

---

## v1.1.7 - 2026-03-09

### Changes
Under the hood changes that will simply future development


---


## v0.1.0 - 2025-01-22

### Added
- First release. Control Yamaha clients on your local network from your Unfolded Circle Remote.
