"""PlayStation coordinator: device data flows to subscribed entity sync_state().

:copyright: (c) 2026 by Jack Powell.
:license: MPL-2.0, see LICENSE for more details.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from functools import partial

from api import PlayStationNetwork
from cloud import (
    CloudAuthorizationError,
    CloudError,
    SonyConsoleService,
    normalize_title_id,
)
from const import PSNConfig
from playdirector import ConsoleController, ConsoleError, account_id_to_base64
from psnawp_api.core.psnawp_exceptions import (
    PSNAWPAuthenticationError,
    PSNAWPUnauthorizedError,
)
from ucapi import media_player
from ucapi_framework.device import PollingDevice

_LOG = logging.getLogger(__name__)
AUTH_ERRORS = (PSNAWPAuthenticationError, PSNAWPUnauthorizedError)


class PSNAccount(PollingDevice):
    """Keep cloud/account, LAN power and controller-session health independent."""

    def __init__(
        self, device_config: PSNConfig, loop=None, config_manager=None, driver=None
    ):
        super().__init__(
            device_config,
            loop,
            poll_interval=10,
            config_manager=config_manager,
            driver=driver,
        )
        self._psn: PlayStationNetwork | None = None
        self._control: ConsoleController | None = None
        self._executor: ThreadPoolExecutor | None = None
        self._connect_lock = asyncio.Lock()
        self._background_tasks: dict[str, asyncio.Task] = {}
        self._next_presence = self._next_trophies = self._next_progress = (
            self._next_inventory
        ) = 0.0
        self._presence_time = 0.0
        self._presence_data = None
        self._local_status: dict = {}
        self._local_reachable: bool | None = None
        self._total_game_count: int | None = None
        self._installed_ids: list[str] = []
        self._installed_metadata: dict[str, dict] = {}
        self._progress_title = ""
        self.psn_state = media_player.States.UNKNOWN
        self.psn_media_title = self.psn_media_title_id = self.psn_media_artist = (
            self.psn_media_image_url
        ) = ""
        self.psn_authenticated: bool | None = None
        self.psn_error = ""
        self.console_error = ""
        self.inventory_error = ""
        self.installed_count: int | None = None
        self.installed_inventory_available = False
        self.trophy_summary: dict[str, int] | None = None
        self.active_trophy_progress: int | None = None

    @property
    def identifier(self) -> str:
        """Stable configured identifier, preserved across credential migration."""
        return self._device_config.identifier

    @property
    def name(self) -> str:
        return self._device_config.name

    @property
    def log_id(self) -> str:
        return self.name

    @property
    def address(self) -> str:
        return self._device_config.ps_device.get("device_ip") or self.identifier

    @property
    def has_control(self) -> bool:
        config = self._device_config.ps_device
        return bool(
            config.get("device_ip") and (config.get("profiles") or config.get("rp_key"))
        )

    @property
    def has_psn(self) -> bool:
        return bool(self._device_config.npsso)

    @property
    def device_type(self) -> str:
        return self._device_config.ps_device.get("device_type", "PSN")

    @property
    def is_on(self) -> bool:
        return self.psn_state in (media_player.States.ON, media_player.States.PLAYING)

    @property
    def can_launch_games(self) -> bool:
        return self.has_psn and (
            self.device_type == "PS5" or bool(self._device_config.launch_device_id)
        )

    @property
    def connection_status(self) -> str:
        if not self.has_control:
            return "PSN only"
        if self._control and self._control.connected:
            return "Remote Play connected"
        if self._local_reachable is False:
            return "Console unreachable"
        if self._local_status.get("status-code") == 620:
            return "Rest mode"
        if self._local_status.get("status-code") == 200:
            return "Console on — controls disconnected"
        return self.console_error or "Checking"

    @property
    def api_health(self) -> str:
        if not self.has_psn:
            return "Not configured"
        if self.psn_authenticated is False:
            return "Authentication expired"
        return (
            "Degraded"
            if self.psn_error or self.inventory_error
            else "Connected"
            if self.psn_authenticated
            else "Checking"
        )

    @property
    def profile_options(self) -> list[str]:
        if self._control:
            return self._control.profiles
        return sorted(self._device_config.ps_device.get("profiles", {}))

    @property
    def selected_profile(self) -> str:
        return self._device_config.ps_device.get("user", "")

    def _save(self, **changes) -> bool:
        previous = copy.deepcopy(self._device_config)
        config = copy.deepcopy(self._device_config)
        for key, value in changes.items():
            setattr(config, key, value)
        if self._config_manager and not self._config_manager.update(config):
            # BaseConfigManager mutates its stored dataclass before writing.
            # Restore memory too when persistence fails.
            get_config = getattr(
                self._config_manager, "get", lambda _: self._device_config
            )
            stored = get_config(self.identifier)
            if stored is not None:
                for key, value in vars(previous).items():
                    setattr(stored, key, value)
            self._device_config = previous
            return False
        self._device_config = config
        return True

    async def _call(self, function, *args, **kwargs):
        """One worker owns all access to the synchronous PSNAWP session."""
        if self._executor is None:
            raise CloudError("PSN account is disconnected")
        return await asyncio.get_running_loop().run_in_executor(
            self._executor, partial(function, *args, **kwargs)
        )

    def _schedule(self, name: str, factory) -> None:
        task = self._background_tasks.get(name)
        if task is None or task.done():
            self._background_tasks[name] = asyncio.create_task(factory())

    async def connect(self) -> bool:
        async with self._connect_lock:
            return True if self.is_connected else await super().connect()

    async def establish_connection(self) -> None:
        if self.has_control:
            self._control = ConsoleController(self._device_config.ps_device)
        if self.has_psn:
            self._executor = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="psn-account"
            )
            self._psn = await self._call(
                PlayStationNetwork, self._device_config.npsso, self._loop
            )
        await self.poll_device()

    def _publish(self) -> None:
        """Derive one observed snapshot; never infer power from command submission."""
        data = (
            self._presence_data
            if time.monotonic() - self._presence_time < 120
            else None
        )
        platform = data.platform if data else {}
        title = dict(data.title_metadata) if data else {}
        if self.has_control:
            code = self._local_status.get("status-code")
            state = (
                media_player.States.ON
                if code == 200
                else media_player.States.OFF
                if code == 620
                else media_player.States.UNAVAILABLE
            )
            # Presence identifies the console platform; title format identifies
            # the game's platform (a PS5 can be running a PS4 game).
            local_id = str(self._local_status.get("running-app-titleid") or "")
            if local_id:
                if str(title.get("npTitleId") or "").upper().removesuffix(
                    "_00"
                ) != local_id.upper().removesuffix("_00"):
                    title = {}
                title = {
                    **title,
                    "npTitleId": local_id,
                    "titleName": self._local_status.get("running-app-name")
                    or title.get("titleName", ""),
                }
            else:
                console_platform = platform.get("platform") or title.get("format")
                if str(console_platform or "").upper() != self.device_type.upper():
                    title = {}
        else:
            online = platform and platform.get("onlineStatus") == "online"
            state = (
                media_player.States.ON
                if online
                else media_player.States.OFF
                if data
                else media_player.States.UNAVAILABLE
            )
        if title.get("npTitleId"):
            title_id = str(title["npTitleId"]).upper().removesuffix("_00")
            cached = self._installed_metadata.get(title_id, {})
            title["titleName"] = title.get("titleName") or cached.get("name", "")
            if not (title.get("conceptIconUrl") or title.get("npTitleIconUrl")):
                title["npTitleIconUrl"] = cached.get("image", "")
            if not title.get("format"):
                title["format"] = (
                    "PS4"
                    if title_id.startswith("CUSA")
                    else "PS5"
                    if title_id.startswith("PPSA")
                    else ""
                )
        if state == media_player.States.ON and title.get("npTitleId"):
            state = media_player.States.PLAYING
        if state != media_player.States.PLAYING:
            title = {}
        previous_title_id = self.psn_media_title_id
        self.psn_state = state
        self._state = str(state)
        self.psn_media_title = title.get("titleName") or ""
        self.psn_media_title_id = title.get("npTitleId") or ""
        self.psn_media_artist = title.get("format") or ""
        self.psn_media_image_url = (
            title.get("conceptIconUrl") or title.get("npTitleIconUrl") or ""
        )
        if self.psn_media_title_id != previous_title_id:
            self.active_trophy_progress = None
            self._next_progress = 0
        self.push_update()

    async def poll_device(self) -> None:
        if self._control:
            try:
                self._local_status = await self._control.status()
                self._local_reachable = bool(self._local_status)
                self.console_error = ""
                credentials = self._control.credentials
                if credentials != self._device_config.ps_device:
                    self._save(
                        ps_device=credentials,
                        console_host_id=credentials.get("host_id", ""),
                        console_name=self._local_status.get("host-name")
                        or self._device_config.console_name,
                    )
            except (ConsoleError, OSError, TimeoutError, ValueError) as exc:
                self.console_error = str(exc)
                self._local_status = {}
                self._local_reachable = False
        now = time.monotonic()
        if self._psn and now >= self._next_presence:
            self._next_presence = now + 45
            self._schedule("presence", self._refresh_presence)
        if self._psn and self.psn_authenticated:
            if now >= self._next_trophies:
                self._next_trophies = now + 1800
                self._schedule("trophies", self._refresh_trophies)
            if self.psn_media_title_id and now >= self._next_progress:
                self._next_progress = now + 300
                self._schedule("progress", self._refresh_progress)
            if self.can_launch_games and now >= self._next_inventory:
                self._next_inventory = now + 900
                self._schedule("inventory", self._refresh_inventory)
        self._publish()

    async def _refresh_presence(self) -> None:
        try:
            if self.psn_authenticated is None:
                user = await self._call(self._psn.get_user)
                expected = self._device_config.psn_account_id
                if expected and account_id_to_base64(expected) != account_id_to_base64(
                    user.account_id
                ):
                    raise PSNAWPAuthenticationError(
                        "PSN account differs from the saved account"
                    )
                self.psn_authenticated = True
                self.push_update()
            data = await self._call(self._psn.get_data)
            expected = self._device_config.psn_account_id
            if expected and account_id_to_base64(expected) != account_id_to_base64(
                data.account_id
            ):
                raise PSNAWPAuthenticationError(
                    "PSN account differs from the saved account"
                )
            self._presence_data, self._presence_time = data, time.monotonic()
            self.psn_authenticated, self.psn_error = True, ""
            if not expected:
                self._save(psn_account_id=data.account_id)
        except AUTH_ERRORS:
            self.psn_authenticated, self.psn_error = False, "PSN authentication expired"
            self._presence_data = None
            self.installed_inventory_available = False
            self.installed_count = None
        except Exception as exc:  # noqa: BLE001 - isolate upstream failures at the service boundary
            self.psn_error = "PSN temporarily unreachable"
            _LOG.warning(
                "[%s] PSN presence unavailable: %s", self.log_id, type(exc).__name__
            )
        self._publish()

    async def _refresh_trophies(self) -> None:
        try:
            self.trophy_summary = await self._call(self._psn.get_trophy_summary)
        except Exception as exc:  # noqa: BLE001 - isolate upstream failures at the service boundary
            _LOG.debug("Trophy totals unavailable: %s", type(exc).__name__)
        self.push_update()

    async def _refresh_progress(self) -> None:
        title_id = self.psn_media_title_id
        try:
            progress = await self._call(self._psn.get_title_progress, title_id)
            if self.psn_media_title_id == title_id:
                self.active_trophy_progress, self._progress_title = progress, title_id
        except Exception as exc:  # noqa: BLE001 - isolate upstream failures at the service boundary
            _LOG.debug("Current game trophies unavailable: %s", type(exc).__name__)
            self._progress_title = title_id
        self.push_update()

    async def _refresh_inventory(self) -> None:
        try:
            service = SonyConsoleService(self._psn.psn.authenticator)
            inventory = await self._call(
                service.inventory,
                self._device_config.launch_device_id,
                self._device_config.console_name,
            )
            self._save(launch_device_id=inventory.duid, console_name=inventory.name)
            self.installed_inventory_available = inventory.titles is not None
            self._installed_ids = list(inventory.titles or [])
            self.installed_count = (
                len(self._installed_ids) if inventory.titles is not None else None
            )
            self.inventory_error = (
                ""
                if inventory.titles is not None
                else "Sony did not return installed-title inventory"
            )
            self.push_update()
        except Exception as exc:  # noqa: BLE001 - isolate upstream failures at the service boundary
            self.installed_inventory_available = False
            self.installed_count = None
            self.inventory_error = (
                str(exc) if isinstance(exc, CloudError) else "PSN inventory unavailable"
            )
        if self.installed_inventory_available and self._installed_ids:
            try:
                metadata = await self._call(
                    self._psn.get_title_metadata, self._installed_ids
                )
                self._installed_metadata.update(metadata)
            except Exception as exc:  # noqa: BLE001 - metadata must not invalidate inventory
                _LOG.debug(
                    "Installed-title metadata unavailable: %s", type(exc).__name__
                )
        self.push_update()

    async def get_game_library(
        self, limit: int = 10, offset: int = 0
    ) -> tuple[list, int | None]:
        if not self._psn or not self.psn_authenticated:
            raise CloudError("PSN authentication is unavailable")
        rows, total = await self._call(self._psn.get_game_page, limit, offset)
        self._total_game_count = total
        return rows, total

    async def get_installed_game_library(
        self, limit: int = 10, offset: int = 0
    ) -> tuple[list[dict], int]:
        if not self.installed_inventory_available:
            raise CloudError(
                self.inventory_error or "Installed titles are not yet available"
            )
        return [
            {
                "title_id": title_id,
                **self._installed_metadata.get(
                    title_id, {"name": title_id, "image": ""}
                ),
            }
            for title_id in self._installed_ids[offset : offset + limit]
        ], len(self._installed_ids)

    async def get_game_details(self, title_id: str):
        if not self._psn:
            raise CloudError("PSN account is unavailable")
        return await self._call(self._psn.get_game_details, title_id)

    async def launch_game(self, title_id: str) -> None:
        if not self.can_launch_games or not self._psn or not self.psn_authenticated:
            raise CloudError("A PSN account and selected PS5 are required")
        title_id = normalize_title_id(title_id)
        duid = self._device_config.launch_device_id
        if not duid:
            await self._refresh_inventory()
            duid = self._device_config.launch_device_id
        service = SonyConsoleService(self._psn.psn.authenticator)
        # An uncertain POST outcome must never be automatically resubmitted.
        try:
            await self._call(service.launch, title_id, duid)
        except CloudAuthorizationError:
            self.inventory_error = "Sony rejected launch authorization"
            self.push_update()
            raise
        self._next_presence = self._next_inventory = 0

    async def select_profile(self, selection: str) -> bool:
        if not self._control:
            raise ConsoleError("No paired console")
        profiles = self._control.credentials.get("profiles", {})
        if self.has_psn and self._device_config.psn_account_id:
            expected = account_id_to_base64(self._device_config.psn_account_id)
            if profiles.get(selection, {}).get("id") != expected:
                raise ValueError("Pairing profile belongs to a different PSN account")
        previous = self._control.profile
        await self._control.select_profile(selection)
        result = self._save(ps_device=copy.deepcopy(self._control.credentials))
        if not result:
            await self._control.select_profile(previous)
        self.push_update()
        return result

    async def _control_command(self, method: str, *args, **kwargs) -> None:
        if not self._control:
            raise ConsoleError("No paired console")
        await getattr(self._control, method)(*args, **kwargs)
        self.push_update()

    async def power_on(self) -> None:
        await self._control_command("wake")

    async def power_off(self) -> None:
        await self._control_command("standby")

    async def power_toggle(self) -> None:
        if self.psn_state in {
            media_player.States.UNKNOWN,
            media_player.States.UNAVAILABLE,
        }:
            raise ConsoleError("Console power state is unknown")
        await (self.power_off() if self.is_on else self.power_on())

    async def go_home(self) -> None:
        await self._control_command("go_home")

    async def send_button(
        self, button: str, action: str = "tap", hold: float = 0.08
    ) -> None:
        await self._control_command("button", button, action, hold)

    async def release_session(self) -> None:
        await self._control_command("release_session", pause_seconds=120)

    async def reconnect_session(self) -> None:
        await self._control_command("reconnect")

    async def disconnect(self) -> None:
        await super().disconnect()
        for task in self._background_tasks.values():
            task.cancel()
        await asyncio.gather(*self._background_tasks.values(), return_exceptions=True)
        self._background_tasks.clear()
        if self._control:
            await self._control.close()
            self._control = None
        if self._psn:
            await self._call(self._psn.close)
            self._psn = None
        if self._executor:
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None
