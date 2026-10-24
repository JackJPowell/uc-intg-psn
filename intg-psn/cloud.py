"""Sony cloud-assisted console inventory and launch adapter.

:copyright: (c) 2026 by Jack Powell.
:license: MPL-2.0, see LICENSE for more details.

Request shapes researched by jstnjx/uc-intg-ps5-connect (MPL-2.0).
All I/O uses the caller's refresh-aware PSNAWP authenticator.
"""

import re
import time
from dataclasses import dataclass

from psnawp_api.core.psnawp_exceptions import (
    PSNAWPForbiddenError,
    PSNAWPUnauthorizedError,
)

BASE = "https://web.np.playstation.com/api/cloudAssistedNavigation/v2/users/me/"


class CloudError(RuntimeError):
    """Cloud data unavailable or command failed."""


class ConsoleSelectionError(CloudError):
    """Console selection must be made explicit."""


class CloudAuthorizationError(CloudError):
    """Sony rejected this account's authorization."""


def normalize_title_id(value: str) -> str:
    """Normalize game identifiers without accepting arbitrary command strings."""
    value = str(value or "").strip().upper()
    if not re.fullmatch(r"[A-Z]{4}\d{5}(?:_00)?", value, flags=re.ASCII):
        raise ValueError("Invalid PlayStation title ID")
    return value.removesuffix("_00")


@dataclass(frozen=True)
class Inventory:
    """Missing inventory is distinct from a known empty title collection."""

    duid: str
    name: str
    titles: tuple[str, ...] | None


class SonyConsoleService:
    """Synchronous service; run only on the account's serialized I/O worker."""

    def __init__(self, authenticator):
        self.authenticator = authenticator

    def _request(self, method: str, path: str, **kwargs) -> dict:
        try:
            response = getattr(self.authenticator, method)(
                url=BASE + path,
                headers={
                    "Accept": "application/json",
                    "User-Agent": "RpNetHttpUtilImpl",
                },
                allow_redirects=False,
                timeout=kwargs.pop("timeout", 10),
                **kwargs,
            )
        except (PSNAWPUnauthorizedError, PSNAWPForbiddenError) as exc:
            raise CloudAuthorizationError(
                "Sony rejected console authorization"
            ) from exc
        if response.status_code in (401, 403):
            raise CloudAuthorizationError("Sony rejected console authorization")
        if not 200 <= response.status_code < 300:
            raise CloudError(
                f"Sony console service returned HTTP {response.status_code}"
            )
        try:
            data = response.json()
        except (ValueError, TypeError) as exc:
            raise CloudError("Sony returned invalid console JSON") from exc
        if not isinstance(data, dict):
            raise CloudError("Sony returned an invalid console response")
        return data

    def consoles(self, include_inventory: bool = False) -> list[dict]:
        """Fetch cloud identities; these DUIDs are distinct from LAN host IDs."""
        result = {}
        for offset in range(0, 100, 10):
            data = self._request(
                "get",
                "clients",
                params={
                    "platform": "PS5",
                    "includeFields": "device,systemData"
                    if include_inventory
                    else "device",
                    "limit": 10,
                    "offset": offset,
                },
            )
            rows = data.get("clients", [])
            if not isinstance(rows, list):
                raise CloudError("Invalid Sony console collection")
            for row in rows:
                if (
                    isinstance(row, dict)
                    and isinstance(row.get("duid"), str)
                    and str(row.get("platform", "PS5")).upper() in {"PS5", "PROSPERO"}
                ):
                    result[row["duid"]] = row
            if len(rows) < 10:
                break
        return list(result.values())

    @staticmethod
    def select(consoles: list[dict], duid: str = "", name: str = "") -> dict:
        """Require an exact saved identity or an unambiguous console match."""
        if duid:
            matches = [row for row in consoles if row["duid"] == duid]
        elif len(consoles) == 1:
            matches = consoles
        else:
            matches = [
                row
                for row in consoles
                if name
                and str((row.get("device") or {}).get("name", "")).casefold()
                == name.casefold()
            ]
        if len(matches) != 1:
            raise ConsoleSelectionError(
                "Choose the console for game launching in integration setup"
            )
        return matches[0]

    def inventory(self, duid: str = "", name: str = "") -> Inventory:
        """Read Sony's per-console installed collection, including apps."""
        console = self.select(self.consoles(True), duid, name)
        system = console.get("systemData") or {}
        installed = system.get("installedTitles") or {}
        rows = installed.get("titles")
        titles = None
        if isinstance(rows, list):
            valid = set()
            for row in rows:
                try:
                    valid.add(normalize_title_id(row["npTitleId"]))
                except (ValueError, TypeError, KeyError):
                    continue
            titles = tuple(sorted(valid))
        return Inventory(
            console["duid"], (console.get("device") or {}).get("name", ""), titles
        )

    def launch(self, title_id: str, duid: str, *, timeout: float = 20) -> None:
        """Submit once, then require completion of the exact returned command."""
        title_id = normalize_title_id(title_id)
        console = self.select(self.consoles(), duid)
        response = self._request(
            "post",
            "commands",
            json={
                "commandDetail": {
                    "platform": "PS5",
                    "duid": console["duid"],
                    "commandType": "launchTitle",
                    "parameters": {"titleIds": [title_id]},
                }
            },
        )
        command_id = response.get("commandId")
        if not isinstance(command_id, str) or not command_id or len(command_id) > 256:
            raise CloudError("Sony did not return a launch command ID")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            data = self._request(
                "get",
                "commands",
                params={
                    "platform": "PS5",
                    "commandIds": command_id,
                    "includeFields": "actions,commandDetail",
                    "commandStatus": "executing,completed",
                },
                timeout=max(0.1, min(5, deadline - time.monotonic())),
            )
            commands = data.get("commands")
            command = (
                next(
                    (row for row in commands if row.get("commandId") == command_id),
                    None,
                )
                if isinstance(commands, list)
                else None
            )
            command = (
                command
                or data.get("command")
                or (data if "commandStatus" in data else {})
            )
            if command.get("commandId", command_id) != command_id:
                raise CloudError("Sony returned another command's status")
            status = str(command.get("commandStatus", "")).lower()
            result = str(command.get("commandResult", "")).lower()
            if status in {"failed", "rejected", "cancelled", "canceled"} or result in {
                "failed",
                "failure",
                "error",
                "rejected",
            }:
                raise CloudError("Console rejected the launch command")
            if status == "completed":
                return
            time.sleep(min(1.5, max(0, deadline - time.monotonic())))
        raise CloudError("Timed out waiting for Sony to confirm launch")
