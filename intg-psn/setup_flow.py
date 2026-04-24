"""
Setup flow for PlayStation Network integration.

:copyright: (c) 2025 by Jack Powell.
:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import logging
from typing import Any

import playdirector
from api import PlayStationNetwork
from const import PSNConfig
from packaging.version import Version
from psnawp_api.utils.misc import parse_npsso_token
from ucapi import IntegrationSetupError, RequestUserInput, SetupError
from ucapi_framework import BaseSetupFlow, EntityMigrationMapping, MigrationData

_LOG = logging.getLogger(__name__)

# Sentinel value for the "enter IP manually" dropdown option
_MANUAL_IP_SENTINEL = "__manual__"

# Sub-step values tracked in self._control_step
_SUB_DEVICE_SELECT = "device_select"
_SUB_PIN = "pin"


class PSNSetupFlow(BaseSetupFlow[PSNConfig]):
    """
    Setup flow for PlayStation Network integration.

    Steps
    -----
    1. NPSSO token entry + optional "Add console control" checkbox.
    2. (If opted in) Network scan → device selection dropdown.
    3. 8-digit PIN entry.
    4. Pair with console → store credentials → return PSNConfig.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._npsso: str = ""
        self._psn_user_id: str = ""
        self._psn_display_name: str = ""
        self._selected_ip: str = ""
        self._selected_device_type: str = ""
        self._control_step: str = ""  # tracks which additional-config sub-step we're on

    # ------------------------------------------------------------------ #
    # Step 1 – NPSSO token entry form
    # ------------------------------------------------------------------ #

    def get_manual_entry_form(self) -> RequestUserInput:
        """Get the NPSSO token entry form (first step)."""
        return self._npsso_form()

    def _npsso_form(self, *, error: str | None = None) -> RequestUserInput:
        """
        Build the NPSSO token entry form, optionally with an inline error message.

        :param error: If set, shown as a warning label above the token field.
        :return: RequestUserInput for NPSSO token
        """
        fields: list[dict[str, Any]] = [
            {
                "id": "info",
                "label": {"en": "Supply your NPSSO Token"},
                "field": {
                    "label": {
                        "value": {
                            "en": (
                                "Your NPSSO Token is required to authenticate "
                                "to the PlayStation Network. "
                                "\n\nPlease sign in to the "
                                "[PlayStation Network](https://playstation.com) first. "
                                "Then [click here]"
                                "(https://ca.account.sony.com/api/v1/ssocookie) "
                                "to retrieve your token."
                            ),
                        }
                    }
                },
            },
        ]
        if error:
            fields.append(
                {
                    "id": "error",
                    "label": {"en": "⚠️ Error"},
                    "field": {"label": {"value": {"en": error}}},
                }
            )
        fields += [
            {
                "field": {"text": {"value": ""}},
                "id": "npsso",
                "label": {"en": "NPSSO Token"},
            },
            {
                "field": {"checkbox": {"value": False}},
                "id": "add_control",
                "label": {
                    "en": "Add power control for my PlayStation",
                },
            },
        ]
        return RequestUserInput({"en": "PlayStation Network Setup"}, fields)

    # ------------------------------------------------------------------ #
    # Step 2 – validate NPSSO, branch on control opt-in
    # ------------------------------------------------------------------ #

    async def query_device(
        self, input_values: dict[str, Any]
    ) -> RequestUserInput | PSNConfig | SetupError:
        """
        Validate the NPSSO token and branch based on the control opt-in checkbox.

        If the user opted in, scan the network and return a device-selection form.
        Otherwise return the finished PSNConfig immediately.

        :param input_values: User input containing 'npsso' token and 'add_control' flag
        :return: Device selection form or finished PSNConfig
        :raises ValueError: If authentication fails
        """
        npsso = parse_npsso_token(input_values.get("npsso", ""))
        if not npsso:
            _LOG.warning("Invalid or missing NPSSO token — re-showing form")
            return self._npsso_form(
                error="Invalid NPSSO token. Please copy it exactly from the link above."
            )

        try:
            _LOG.debug("Connecting to PSN API")
            psnawp = PlayStationNetwork(npsso, self.driver.loop)
            user = psnawp.get_user()
            _LOG.info("Authenticated PSN Account: %s", user.online_id)
        except Exception as err:
            _LOG.error("Failed to authenticate with PSN: %s", err)
            return SetupError(error_type=IntegrationSetupError.AUTHORIZATION_ERROR)

        self._npsso = npsso
        self._psn_user_id = user.account_id
        self._psn_display_name = user.online_id

        if not input_values.get("add_control", False):
            return PSNConfig(
                identifier=self._psn_user_id,
                name=self._psn_display_name,
                npsso=self._npsso,
            )

        # Control opted in — store a partial config so the framework routes
        # subsequent responses to handle_additional_configuration_response.
        self._pending_device_config = PSNConfig(
            identifier=self._psn_user_id,
            name=self._psn_display_name,
            npsso=self._npsso,
        )
        self._control_step = _SUB_DEVICE_SELECT
        return await self._build_device_select_form()

    # ------------------------------------------------------------------ #
    # Additional configuration routing (framework hook)
    # ------------------------------------------------------------------ #

    async def handle_additional_configuration_response(
        self, msg: Any
    ) -> RequestUserInput | PSNConfig | SetupError | None:
        """
        Handle responses from device-select and PIN screens.

        Routed by self._control_step:
          _SUB_DEVICE_SELECT → validate choice, return PIN form
          _SUB_PIN           → pair, return final PSNConfig
        """
        if self._control_step == _SUB_DEVICE_SELECT:
            return await self._handle_device_select(msg.input_values)
        if self._control_step == _SUB_PIN:
            return await self._handle_pin(msg.input_values)
        _LOG.error("Unknown control sub-step: %r", self._control_step)
        return None

    # ------------------------------------------------------------------ #
    # Device-selection form (scan)
    # ------------------------------------------------------------------ #

    async def _build_device_select_form(self) -> RequestUserInput:
        """Scan the local network and return the device-selection RequestUserInput."""
        _LOG.debug("Scanning network for PlayStation devices")
        discovered: list[playdirector.DiscoveredDevice] = []
        async for device in playdirector.scan():
            discovered.append(device)
            _LOG.debug(
                "Found: %s (%s) at %s", device.name, device.device_type, device.ip
            )

        items: list[dict[str, Any]] = [
            {
                "id": f"{d.ip}|{d.device_type}",
                "label": {"en": f"{d.name} ({d.device_type}) — {d.ip}"},
            }
            for d in discovered
        ]
        items.append(
            {"id": _MANUAL_IP_SENTINEL, "label": {"en": "Enter IP address manually"}}
        )

        default_id = items[0]["id"]
        fields: list[dict[str, Any]] = [
            {
                "id": "device_choice",
                "label": {"en": "Select your PlayStation"},
                "field": {
                    "dropdown": {
                        "value": default_id,
                        "items": items,
                    }
                },
            },
        ]

        return RequestUserInput({"en": "Select PlayStation Console"}, fields)

    async def _handle_device_select(
        self, input_values: dict[str, Any]
    ) -> RequestUserInput:
        """Validate device selection and return the appropriate PIN entry form.

        If a discovered device was chosen the IP and type are stored now and
        the next screen shows only the PIN field.  If the user chose manual
        entry the next screen includes an IP address field alongside the PIN.
        """
        choice = input_values.get("device_choice", _MANUAL_IP_SENTINEL)

        if choice == _MANUAL_IP_SENTINEL:
            # IP unknown — ask for it together with the PIN on the next screen.
            self._control_step = _SUB_PIN
            return self._manual_pin_form()

        # A discovered device was selected — store ip/type now.
        ip, _, device_type = choice.partition("|")
        self._selected_ip = ip
        self._selected_device_type = device_type
        _LOG.debug(
            "Selected device: %s (%s)", self._selected_ip, self._selected_device_type
        )

        self._control_step = _SUB_PIN
        return self._pin_form(device_type=device_type)

    # ------------------------------------------------------------------ #
    # PIN step → pair → finish
    # ------------------------------------------------------------------ #

    async def _handle_pin(
        self, input_values: dict[str, Any]
    ) -> RequestUserInput | PSNConfig | SetupError:
        """Pair with the console and return the finished PSNConfig."""
        pin = input_values.get("pin", "").strip().replace(" ", "").replace("-", "")

        # Manual path: IP + PIN were on the same screen.
        if not self._selected_ip:
            manual_ip = input_values.get("manual_ip", "").strip()
            if not manual_ip:
                _LOG.warning("Manual IP missing — re-showing form")
                return self._manual_pin_form(
                    error="Please enter the IP address of your PlayStation."
                )
            if len(pin) != 8 or not pin.isdigit():
                _LOG.warning("Invalid PIN — re-showing form")
                return self._manual_pin_form(
                    prefill_ip=manual_ip,
                    error="PIN must be exactly 8 digits.",
                )
            self._selected_ip = manual_ip
            device = await playdirector.find(manual_ip, timeout=5.0)
            self._selected_device_type = str(device.device_type) if device else "PS5"
            _LOG.debug(
                "Manual device: %s (%s)", self._selected_ip, self._selected_device_type
            )
        else:
            if len(pin) != 8 or not pin.isdigit():
                _LOG.warning("Invalid PIN — re-showing form")
                return self._pin_form(
                    device_type=self._selected_device_type,
                    error="PIN must be exactly 8 digits.",
                )

        _LOG.debug(
            "Pairing with %s (%s)", self._selected_ip, self._selected_device_type
        )
        try:
            credential = await playdirector.pair(
                self._selected_ip,
                pin=pin,
                npsso=self._npsso,
            )
        except Exception as err:
            _LOG.error("Pairing failed: %s", err)
            return SetupError(error_type=IntegrationSetupError.OTHER)

        ps_device = credential.to_dict()
        ps_device["device_ip"] = self._selected_ip
        ps_device["device_type"] = self._selected_device_type

        _LOG.info(
            "Successfully paired with %s (%s)",
            self._selected_ip,
            self._selected_device_type,
        )

        return PSNConfig(
            identifier=self._psn_user_id,
            name=self._psn_display_name,
            npsso=self._npsso,
            ps_device=ps_device,
        )

    # ------------------------------------------------------------------ #
    # Form builder helpers
    # ------------------------------------------------------------------ #

    def _pin_form(
        self, *, device_type: str, error: str | None = None
    ) -> RequestUserInput:
        """PIN-only form used when a device was selected from the dropdown."""
        if device_type == "PS5":
            pin_help = (
                "On your PS5 go to **Settings → System → Remote Play → Pair Device** "
                "and note the 8-digit PIN shown on screen."
            )
        else:
            pin_help = (
                "On your PS4 go to **Settings → Remote Play Connection Settings → Add Device** "
                "and note the 8-digit PIN shown on screen."
            )
        fields: list[dict[str, Any]] = [
            {
                "id": "pin_info",
                "label": {"en": "Finding your PIN"},
                "field": {"label": {"value": {"en": pin_help}}},
            },
        ]
        if error:
            fields.append(
                {
                    "id": "error",
                    "label": {"en": "⚠️ Error"},
                    "field": {"label": {"value": {"en": error}}},
                }
            )
        fields.append(
            {
                "id": "pin",
                "label": {"en": "8-digit PIN"},
                "field": {"text": {"value": ""}},
            }
        )
        return RequestUserInput({"en": "Enter PlayStation PIN"}, fields)

    def _manual_pin_form(
        self, *, prefill_ip: str = "", error: str | None = None
    ) -> RequestUserInput:
        """IP + PIN form used when the user chose manual entry."""
        fields: list[dict[str, Any]] = [
            {
                "id": "manual_ip",
                "label": {"en": "PlayStation IP address"},
                "field": {"text": {"value": prefill_ip}},
            },
        ]
        if error:
            fields.append(
                {
                    "id": "error",
                    "label": {"en": "⚠️ Error"},
                    "field": {"label": {"value": {"en": error}}},
                }
            )
        fields += [
            {
                "id": "pin_info",
                "label": {"en": "Finding your PIN"},
                "field": {
                    "label": {
                        "value": {
                            "en": (
                                "On your PlayStation go to "
                                "**Settings → Remote Play → Pair Device** "
                                "(PS5) or "
                                "**Settings → Remote Play Connection Settings → Add Device** "
                                "(PS4) and enter the 8-digit PIN shown."
                            )
                        }
                    }
                },
            },
            {
                "id": "pin",
                "label": {"en": "8-digit PIN"},
                "field": {"text": {"value": ""}},
            },
        ]
        return RequestUserInput({"en": "Enter PlayStation Details"}, fields)

    # ------------------------------------------------------------------ #
    # Migration helpers (unchanged)
    # ------------------------------------------------------------------ #

    async def is_migration_required(self, previous_version: str) -> bool:
        """
        Check if migration is required for existing configuration.

        :param previous_version: Previous version of the integration
        :return: True if migration is required, False otherwise
        """
        if Version(previous_version) <= Version("1.1.0"):
            return True
        return False

    async def get_migration_data(
        self, previous_version: str, current_version: str
    ) -> MigrationData:
        """Generate entity ID mappings for migration.

        Returns:
            MigrationData with driver IDs and entity mappings
        """
        mappings: list[EntityMigrationMapping] = []

        for device in self.config.all():
            mappings.append(
                {
                    "previous_entity_id": f"{device.identifier}",
                    "new_entity_id": f"media_player.{device.identifier}",
                }
            )

        return {
            "previous_driver_id": "psn_driver",
            "new_driver_id": "psn_driver",
            "entity_mappings": mappings,
        }
