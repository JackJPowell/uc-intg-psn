"""Configure PSN and optional local Remote Play without changing saved IDs.

:copyright: (c) 2026 by Jack Powell.
:license: MPL-2.0, see LICENSE for more details.
"""

import asyncio
import copy
import logging
from dataclasses import replace
from typing import Any

from api import PlayStationNetwork
from cloud import SonyConsoleService
from const import PSNConfig
from packaging.version import Version
from playdirector import account_id_to_base64, pair_console, scan
from psnawp_api.core.psnawp_exceptions import PSNAWPInvalidTokenError
from psnawp_api.utils.misc import parse_npsso_token
from pyremoteplay.ddp import async_search
from ucapi import IntegrationSetupError, RequestUserInput, SetupError
from ucapi_framework import BaseSetupFlow, MigrationData

_LOG = logging.getLogger(__name__)
_MANUAL = "__manual__"
_RESCAN = "__rescan__"
_SKIP = "__skip__"


def _enabled(value: Any) -> bool:
    return (
        value is True
        or isinstance(value, str)
        and value.lower() in {"true", "1", "on", "yes"}
    )


def _text(key: str, label: str, value: str = "") -> dict:
    return {"id": key, "label": {"en": label}, "field": {"text": {"value": value}}}


def _checkbox(key: str, label: str, value: bool = False) -> dict:
    return {"id": key, "label": {"en": label}, "field": {"checkbox": {"value": value}}}


def _message(text: str) -> dict:
    return {
        "id": "information",
        "label": {"en": "Information"},
        "field": {"label": {"value": {"en": text}}},
    }


def _reported_model(data: dict) -> str:
    """Show supplied model text without inferring Pro from platform or host ID."""
    for key in ("modelName", "modelNumber", "model", "host-model"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _local_console_label(row: dict) -> str:
    name = row.get("host-name") or "PlayStation"
    model = _reported_model(row) or row.get("host-type") or ""
    details = [name]
    if model and model.strip().casefold() != str(name).strip().casefold():
        details.append(model)
    details.append(row["host-ip"])
    status = {200: "On", 620: "Rest mode"}.get(row.get("status-code"))
    if status:
        details.append(status)
    if row.get("running-app-name"):
        details.append(f"Playing: {row['running-app-name']}")
    return " — ".join(str(value) for value in details if value)


def _cloud_console_label(row: dict) -> str:
    device = row.get("device") or {}
    name = device.get("name") or "PlayStation 5"
    model = _reported_model(device) or _reported_model(row)
    if model and model.casefold() != name.casefold():
        return f"{name} — {model}"
    return name


class PSNSetupFlow(BaseSetupFlow[PSNConfig]):
    """Support PSN-only, paired consoles, credential refresh and extra profiles."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._selected_ip = ""
        self._profile_name = "paired-user"
        self._add_profile = False
        self._stage = ""

    def get_manual_entry_form(self) -> RequestUserInput:
        existing = self.selected_config_entry
        self._setup_mode = (
            "psn_console" if not existing or existing.ps_device else "psn_only"
        )
        if existing and not existing.npsso:
            self._setup_mode = "local_only"
        return RequestUserInput(
            {"en": "Set up PlayStation"},
            [
                _message(
                    "Choose what you want to connect. PSN setup retrieves your account details automatically."
                ),
                {
                    "id": "setup_mode",
                    "label": {"en": "Connection"},
                    "field": {
                        "dropdown": {
                            "value": self._setup_mode,
                            "items": [
                                {
                                    "id": "psn_console",
                                    "label": {
                                        "en": "PSN and console controls (recommended)"
                                    },
                                },
                                {
                                    "id": "psn_only",
                                    "label": {
                                        "en": "PSN only — game history and trophies"
                                    },
                                },
                                {
                                    "id": "local_only",
                                    "label": {
                                        "en": "Console controls without PSN (advanced)"
                                    },
                                },
                            ],
                        }
                    },
                },
            ],
        )

    def _form(self, error: str = "") -> RequestUserInput:
        existing = self.selected_config_entry
        local_only = getattr(self, "_setup_mode", "psn_console") == "local_only"
        if local_only:
            fields = [
                _message(
                    "This advanced option needs Sony's numeric account ID or its Base64 form from an existing pairing. "
                    "It is not your PSN online name. Choose PSN setup instead to retrieve it automatically. "
                    "Game history, trophies and cloud launching are unavailable without PSN authentication."
                ),
                _text(
                    "account_id",
                    "Existing PSN account ID",
                    existing.psn_account_id if existing else "",
                ),
            ]
        else:
            fields = [
                _message(
                    "1. [Sign in to PlayStation Network](https://www.playstation.com/) in your browser. "
                    "\n\n2. In the same browser, [retrieve your NPSSO token](https://ca.account.sony.com/api/v1/ssocookie). "
                    "\n\n3. Paste the token or the complete JSON response below. Your account ID is retrieved automatically."
                    + (
                        " Leave this blank to keep your saved PSN credentials."
                        if existing and existing.npsso
                        else ""
                    )
                ),
                _text("npsso", "NPSSO token or JSON response"),
            ]
        if error:
            fields.append(
                {
                    "id": "error",
                    "label": {"en": "Please check"},
                    "field": {"label": {"value": {"en": error}}},
                }
            )
        fields.append(
            _text("name", "Display name", existing.name if existing else "PlayStation")
        )
        if existing:
            fields.append(
                _checkbox(
                    "add_control", "Enable console controls", bool(existing.ps_device)
                )
            )
        if existing and existing.ps_device:
            fields.extend(
                [
                    _checkbox("re_pair", "Pair this console again"),
                    _checkbox(
                        "add_profile", "Register an additional controller profile"
                    ),
                    _text(
                        "profile_name",
                        "Name for the additional profile",
                        "additional-user",
                    ),
                ]
            )
        title = "Pair without PSN" if local_only else "Connect PlayStation Network"
        return RequestUserInput({"en": title}, fields)

    async def query_device(self, input_values: dict[str, Any]):
        existing = self.selected_config_entry
        if "setup_mode" in input_values:
            mode = input_values["setup_mode"]
            if mode not in {"psn_console", "psn_only", "local_only"}:
                return self.get_manual_entry_form()
            self._setup_mode = mode
            return self._form()
        mode = getattr(self, "_setup_mode", "psn_console")
        config = copy.deepcopy(existing) if existing else PSNConfig("", "PlayStation")
        control_requested = _enabled(
            input_values.get("add_control", mode != "psn_only")
        )
        self._cloud_consoles = []
        token_input = str(input_values.get("npsso") or "").strip()
        try:
            token = parse_npsso_token(token_input) if token_input else config.npsso
        except (PSNAWPInvalidTokenError, TypeError):
            return self._form("The NPSSO token JSON is malformed")
        if mode == "local_only" or _enabled(input_values.get("disable_psn")):
            token = ""
        if token_input and not token and not _enabled(input_values.get("disable_psn")):
            return self._form("The NPSSO token is invalid")
        if (
            mode != "local_only"
            and not token
            and not _enabled(input_values.get("disable_psn"))
        ):
            return self._form(
                "Paste your NPSSO token or JSON response from the link above."
            )
        account_id = str(
            input_values.get("account_id")
            or config.psn_account_id
            or config.ps_device.get("account_id")
            or config.ps_device.get("profiles", {})
            .get(config.ps_device.get("user", ""), {})
            .get("id", "")
        ).strip()
        display_name = str(input_values.get("name") or config.name).strip()
        if token:

            def validate():
                api = PlayStationNetwork(token, self.driver.loop)
                try:
                    user = api.get_user()
                    consoles = []
                    if control_requested and not config.launch_device_id:
                        try:
                            consoles = SonyConsoleService(
                                api.psn.authenticator
                            ).consoles()
                        except Exception as exc:  # noqa: BLE001 - cloud launch must not block local pairing
                            _LOG.debug(
                                "Cloud console discovery unavailable: %s",
                                type(exc).__name__,
                            )
                    return str(user.account_id), user.online_id, consoles
                finally:
                    api.close()

            try:
                cloud_id, online_id, self._cloud_consoles = await asyncio.to_thread(
                    validate
                )
                if config.psn_account_id and account_id_to_base64(
                    config.psn_account_id
                ) != account_id_to_base64(cloud_id):
                    return self._form("This token belongs to a different PSN account")
                if account_id and account_id_to_base64(
                    account_id
                ) != account_id_to_base64(cloud_id):
                    return self._form("Account ID does not match the PSN token")
                account_id = cloud_id
                if display_name == "PlayStation":
                    display_name = online_id
            except Exception as exc:  # noqa: BLE001 - isolate upstream failures at the service boundary
                _LOG.warning("PSN setup authentication failed: %s", type(exc).__name__)
                return self._form(
                    "Could not sign in to PSN. Sign in using the link above and retrieve a fresh NPSSO token."
                )
        try:
            normalized = account_id_to_base64(account_id)
        except ValueError:
            return self._form("Enter a valid NPSSO token or PSN account ID")
        config.npsso, config.psn_account_id, config.name = (
            token or "",
            account_id,
            display_name,
        )
        config.launch_device_id = str(
            input_values.get("launch_device_id", config.launch_device_id) or ""
        ).strip()
        self._profile_name = str(
            input_values.get("profile_name") or "paired-user"
        ).strip()
        if not self._profile_name or len(self._profile_name) > 80:
            return self._form("Profile name must contain between one and 80 characters")
        self._add_profile = _enabled(input_values.get("add_profile"))
        if self._add_profile and self._profile_name in config.ps_device.get(
            "profiles", {}
        ):
            return self._form("Choose a new name for the additional profile")
        control = control_requested or self._add_profile
        if not token and not control and not config.ps_device:
            return self._form(
                "Enable console controls when using an account ID without a PSN token"
            )
        if not control or (
            config.ps_device
            and not _enabled(input_values.get("re_pair"))
            and not self._add_profile
        ):
            if not config.identifier:
                config.identifier = (
                    account_id
                    if account_id.isdecimal()
                    else "psn_"
                    + normalized.replace("/", "_").replace("+", "-").rstrip("=")
                )
            return (
                self._finish_console(config) if config.ps_device and control else config
            )
        self._pending_device_config = config
        if config.ps_device:
            self._selected_ip = config.ps_device["device_ip"]
            self._stage = "pin"
            return self._pin_form()
        return await self._discover_consoles()

    async def _discover_consoles(self) -> RequestUserInput:
        self._stage = "select"
        try:
            rows = await scan(timeout=5)
        except (OSError, TimeoutError, RuntimeError) as exc:
            _LOG.warning("Console discovery failed: %s", type(exc).__name__)
            rows = []
        if not rows:
            try:
                rows = await async_search(timeout=5, directed=True)
            except (OSError, TimeoutError, RuntimeError) as exc:
                _LOG.warning("Interface discovery failed: %s", type(exc).__name__)
        _LOG.info("Console discovery found %d console(s)", len(rows))
        options = [
            {
                "id": row["host-ip"],
                "label": {"en": _local_console_label(row)},
            }
            for row in rows
            if row.get("host-ip")
        ]
        found = bool(options)
        options.extend(
            [
                {"id": _RESCAN, "label": {"en": "Search again"}},
                {"id": _MANUAL, "label": {"en": "Enter IP address manually"}},
            ]
        )
        return RequestUserInput(
            {"en": "Select your PlayStation"},
            [
                _message(
                    "Match the IP address with Settings → Network → Connection Status → View Connection Status on your PS5. "
                    "The list also shows whether each console is on or in rest mode when reported. "
                    "You can give each console a distinct name in Settings → System → System Software → Console Information."
                    if found
                    else "No consoles were found. On PS5, enable Settings → System → Remote Play → Enable Remote Play. "
                    "Keep the console on and connect it to the same network as the integration. "
                    "Search again, or enter its IP address from Settings → Network → Connection Status. "
                    "When running in Docker, use host networking for discovery."
                ),
                {
                    "id": "device_choice",
                    "label": {"en": "PlayStation"},
                    "field": {
                        "dropdown": {"value": options[0]["id"], "items": options}
                    },
                },
            ],
        )

    def _finish_console(self, config):
        rows = getattr(self, "_cloud_consoles", [])
        if (
            config.npsso
            and config.ps_device.get("device_type") == "PS5"
            and not config.launch_device_id
            and rows
        ):
            if len(rows) == 1:
                return replace(config, launch_device_id=rows[0]["duid"])
            self._pending_device_config = config
            self._stage = "cloud_select"
            options = [
                {"id": row["duid"], "label": {"en": _cloud_console_label(row)}}
                for row in rows
            ]
            options.append(
                {"id": _SKIP, "label": {"en": "Skip cloud game launching for now"}}
            )
            return RequestUserInput(
                {"en": "Choose the console for game launching"},
                [
                    _message(
                        "Your PSN account has multiple consoles. Choose the one you just paired. "
                        "If the names are identical, you can give each PS5 a distinct name under "
                        "Settings → System → System Software → Console Information."
                    ),
                    {
                        "id": "cloud_console",
                        "label": {"en": "Console"},
                        "field": {
                            "dropdown": {"value": options[0]["id"], "items": options}
                        },
                    },
                ],
            )
        return config

    def _pin_form(self, error: str = "") -> RequestUserInput:
        fields = [
            _message(
                error
                or "PS5: Settings → System → Remote Play → Link Device. "
                "PS4: Settings → Remote Play Connection Settings → Add Device. Enter the eight-digit PIN."
            )
        ]
        if not self._selected_ip:
            fields.append(_text("manual_ip", "Console IP address"))
        fields.append(_text("pin", "Remote Play PIN"))
        return RequestUserInput({"en": "Pair PlayStation"}, fields)

    async def handle_additional_configuration_response(self, msg):
        values = msg.input_values
        if self._stage == "cloud_select":
            choice = str(values.get("cloud_console") or "")
            if choice == _SKIP:
                return self._pending_device_config
            if choice not in {row["duid"] for row in self._cloud_consoles}:
                return self._finish_console(self._pending_device_config)
            return replace(self._pending_device_config, launch_device_id=choice)
        if self._stage == "select":
            choice = str(values.get("device_choice") or _MANUAL)
            if choice == _RESCAN:
                return await self._discover_consoles()
            self._selected_ip = "" if choice == _MANUAL else choice
            self._stage = "pin"
            return self._pin_form()
        if self._stage != "pin":
            return SetupError(error_type=IntegrationSetupError.OTHER)
        host = self._selected_ip or str(values.get("manual_ip") or "").strip()
        pin = str(values.get("pin") or "").replace(" ", "").replace("-", "")
        config = self._pending_device_config
        try:
            credentials = await pair_console(
                host, config.psn_account_id, pin, self._profile_name
            )
            old_host = config.ps_device.get("host_id") or config.ps_device.get(
                "device_id"
            )
            if (
                old_host
                and str(old_host).replace(":", "").casefold()
                != credentials["host_id"].replace(":", "").casefold()
            ):
                return self._pin_form(
                    "The selected console differs from the saved console"
                )
            if self._add_profile:
                credentials["profiles"] = {
                    **config.ps_device.get("profiles", {}),
                    **credentials["profiles"],
                }
        except (ValueError, RuntimeError, OSError, TimeoutError) as exc:
            _LOG.warning("Console registration failed: %s", type(exc).__name__)
            return self._pin_form(
                "Pairing failed. Check the console IP and generate a fresh eight-digit PIN."
            )
        identifier = (
            config.identifier
            or f"console_{credentials['host_id'].replace(':', '').lower()}"
        )
        return self._finish_console(
            replace(
                config,
                identifier=identifier,
                ps_device=credentials,
                console_host_id=credentials["host_id"],
                console_name=credentials.get("host_name", ""),
            )
        )

    async def is_migration_required(self, previous_version: str) -> bool:
        return Version(previous_version) <= Version("1.1.0")

    async def get_migration_data(
        self, previous_version: str, current_version: str
    ) -> MigrationData:
        return {
            "previous_driver_id": "psn_driver",
            "new_driver_id": "psn_driver",
            "entity_mappings": [
                {
                    "previous_entity_id": item.identifier,
                    "new_entity_id": f"media_player.{item.identifier}",
                }
                for item in self.config.all()
            ],
        }
