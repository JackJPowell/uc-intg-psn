"""Exercise setup pages and persistence through the framework's input router."""

import asyncio
import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from const import PSNConfig
from playdirector import ConsoleError
from psnawp_api.core.psnawp_exceptions import PSNAWPInvalidTokenError
from setup_flow import PSNSetupFlow
from ucapi import RequestUserInput, SetupComplete
from ucapi_framework.setup import SetupSteps

TOKEN = "a" * 64
NEW_TOKEN = "b" * 64
HOST = "192.0.2.2"


def pairing(host_id="112233445566", profile="paired-user"):
    return {
        "device_ip": HOST,
        "device_type": "PS5",
        "host_id": host_id,
        "host_name": "Office PS5",
        "user": profile,
        "profiles": {profile: {"id": "ewAAAAAAAAA=", "hosts": {host_id: {}}}},
    }


def saved_console():
    return PSNConfig(
        "first",
        "Living Room",
        TOKEN,
        ps_device=pairing("aabbccddeeff"),
        psn_account_id="123",
        launch_device_id="first-cloud",
        console_host_id="aabbccddeeff",
    )


def assert_page(result, *fields):
    assert isinstance(result, RequestUserInput)
    assert set(fields) <= {row["id"] for row in result.settings}


class SetupSession:
    def __init__(self, monkeypatch, saved=(), selected=None):
        self.entries = {entry.identifier: entry for entry in saved}
        self.manager = SimpleNamespace(
            all=lambda: list(self.entries.values()),
            get=self.entries.get,
            contains=lambda key: key in self.entries,
            add_or_update=Mock(
                side_effect=lambda entry: self.entries.update({entry.identifier: entry})
            ),
            remove=Mock(side_effect=lambda key: self.entries.pop(key, None)),
        )
        self.flow = PSNSetupFlow(
            self.manager, driver=SimpleNamespace(loop=asyncio.get_running_loop())
        )
        self.flow._selected_config_id = selected
        self.flow._add_mode = selected is None
        self.flow._await_setup_completion = AsyncMock()
        self.scan = AsyncMock(
            return_value=[{"host-ip": HOST, "host-name": "Office PS5"}]
        )
        self.fallback_scan = AsyncMock(return_value=[])
        self.pair = AsyncMock(return_value=pairing())
        self.api = SimpleNamespace(
            get_user=Mock(
                return_value=SimpleNamespace(account_id="123", online_id="Player")
            ),
            psn=SimpleNamespace(authenticator=object()),
            close=Mock(),
        )
        self.create_api = Mock(return_value=self.api)
        self.consoles = Mock(return_value=[])
        monkeypatch.setattr("setup_flow.scan", self.scan)
        monkeypatch.setattr("setup_flow.async_search", self.fallback_scan)
        monkeypatch.setattr("setup_flow.pair_console", self.pair)
        monkeypatch.setattr("setup_flow.PlayStationNetwork", self.create_api)
        monkeypatch.setattr(
            "setup_flow.SonyConsoleService",
            lambda auth: SimpleNamespace(consoles=self.consoles),
        )

    async def start(self, mode):
        welcome = await self.flow._handle_manual_entry()
        assert_page(welcome, "setup_mode")
        form = await self.submit(setup_mode=mode)
        assert_page(form, "account_id" if mode == "local_only" else "npsso")
        return form

    async def submit(self, **values):
        return await self.flow._handle_user_data_response(
            SimpleNamespace(input_values=values)
        )

    def assert_unsaved(self):
        self.manager.add_or_update.assert_not_called()
        self.manager.remove.assert_not_called()
        self.flow._await_setup_completion.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["add", "update"])
async def test_configuration_menu_opens_connection_choices(monkeypatch, action):
    first = saved_console()
    session = SetupSession(monkeypatch, [first])
    session.flow._setup_step = SetupSteps.CONFIGURATION_MODE
    result = await session.submit(action=action, choice=first.identifier)
    assert_page(result, "setup_mode")
    assert session.flow.selected_config_entry is (first if action == "update" else None)
    session.assert_unsaved()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["psn_console", "psn_only", "local_only"])
@pytest.mark.parametrize("manual", [False, True])
async def test_new_device_modes_complete_with_only_requested_services(
    monkeypatch, mode, manual
):
    session = SetupSession(monkeypatch)
    await session.start(mode)
    result = await session.submit(
        **({"account_id": "123"} if mode == "local_only" else {"npsso": TOKEN})
    )
    if mode != "psn_only":
        assert_page(result, "device_choice")
        session.assert_unsaved()
        result = await session.submit(device_choice="__manual__" if manual else HOST)
        assert_page(result, "manual_ip", "pin")
        session.assert_unsaved()
        result = await session.submit(manual_ip=HOST, pin="0012-3456")
        session.pair.assert_awaited_once_with(HOST, "123", "00123456", "paired-user")
    else:
        session.scan.assert_not_awaited()
        session.pair.assert_not_awaited()
        session.consoles.assert_not_called()
    assert isinstance(result, SetupComplete)
    assert len(session.entries) == 1
    saved = next(iter(session.entries.values()))
    assert saved.psn_account_id == "123"
    assert bool(saved.ps_device) == (mode != "psn_only")
    assert saved.npsso == ("" if mode == "local_only" else TOKEN)
    if mode == "local_only":
        session.create_api.assert_not_called()
    else:
        session.create_api.assert_called_once_with(TOKEN, session.flow.driver.loop)
        session.api.close.assert_called_once()
    session.manager.add_or_update.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("account", ["saved", "different"])
@pytest.mark.parametrize("cloud_choice", ["first-cloud", "second-cloud", "__skip__"])
async def test_second_console_account_and_cloud_choices_are_independent(
    monkeypatch, account, cloud_choice
):
    first = saved_console()
    original = copy.deepcopy(first)
    session = SetupSession(monkeypatch, [first])
    session.consoles.return_value = [
        {"duid": "first-cloud", "device": {"name": "Living Room"}},
        {"duid": "second-cloud", "device": {"name": "Office"}},
    ]
    await session.start("psn_console")
    different = account == "different"
    if different:
        session.api.get_user.return_value.account_id = "456"
    result = await session.submit(
        psn_account="__new_account__" if different else first.identifier,
        npsso=NEW_TOKEN if different else "",
    )
    assert_page(result, "device_choice")
    await session.submit(device_choice=HOST)
    result = await session.submit(pin="12345678")
    assert_page(result, "cloud_console")
    session.assert_unsaved()
    result = await session.submit(cloud_console=cloud_choice)
    assert isinstance(result, SetupComplete)
    assert first == original
    second = session.entries["console_112233445566"]
    assert second.psn_account_id == ("456" if different else "123")
    assert second.npsso == (NEW_TOKEN if different else TOKEN)
    assert second.launch_device_id == (
        "" if cloud_choice == "__skip__" else cloud_choice
    )
    assert second.ps_device == pairing()
    session.manager.remove.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation", ["credentials", "re_pair", "add_profile", "change_cloud_console"]
)
async def test_existing_device_update_options_preserve_identity(monkeypatch, operation):
    first = saved_console()
    original = copy.deepcopy(first)
    session = SetupSession(monkeypatch, [first], selected=first.identifier)
    await session.start("psn_console")
    session.consoles.return_value = [
        {"duid": "first-cloud", "device": {"name": "Living Room"}},
        {"duid": "second-cloud", "device": {"name": "Office"}},
    ]
    session.pair.return_value = pairing(
        "aabbccddeeff", "extra" if operation == "add_profile" else "paired-user"
    )
    values = {"npsso": NEW_TOKEN, "name": "Renamed"}
    if operation != "credentials":
        values[operation] = True
    if operation == "add_profile":
        values["profile_name"] = "extra"
    result = await session.submit(**values)
    if operation in {"re_pair", "add_profile"}:
        assert_page(result, "pin", "manual_ip")
        session.assert_unsaved()
        assert first == original
        result = await session.submit(pin="12345678")
    elif operation == "change_cloud_console":
        assert_page(result, "cloud_console")
        session.assert_unsaved()
        assert first == original
        result = await session.submit(cloud_console="second-cloud")
    assert isinstance(result, SetupComplete)
    saved = session.entries[first.identifier]
    assert saved.identifier == original.identifier
    assert saved.name == "Renamed"
    assert saved.npsso == NEW_TOKEN
    assert saved.console_host_id == original.console_host_id
    assert saved.launch_device_id == (
        "second-cloud" if operation == "change_cloud_console" else "first-cloud"
    )
    if operation == "add_profile":
        assert set(saved.ps_device["profiles"]) == {"paired-user", "extra"}
        assert saved.ps_device["user"] == "extra"
    elif operation == "re_pair":
        session.pair.assert_awaited_once()
    else:
        assert saved.ps_device == original.ps_device
        session.pair.assert_not_awaited()
    session.manager.remove.assert_called_once_with(first.identifier)
    session.manager.add_or_update.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["new", "re_pair", "add_profile"])
@pytest.mark.parametrize(
    "failure",
    [
        ValueError("bad PIN"),
        ConsoleError("PIN rejected"),
        OSError("offline"),
        TimeoutError(),
    ],
)
async def test_failed_registration_returns_pairing_page_and_retry_completes(
    monkeypatch, operation, failure
):
    first = saved_console()
    original = copy.deepcopy(first)
    session = SetupSession(
        monkeypatch, [first], selected=None if operation == "new" else first.identifier
    )
    await session.start("psn_console")
    values = {"psn_account": first.identifier}
    if operation != "new":
        values[operation] = True
    if operation == "add_profile":
        values["profile_name"] = "extra"
    result = await session.submit(**values)
    if operation == "new":
        result = await session.submit(device_choice=HOST)
    assert_page(result, "pin")
    pending = copy.deepcopy(session.flow._pending_device_config)
    credentials = pairing(
        "112233445566" if operation == "new" else "aabbccddeeff",
        "extra" if operation == "add_profile" else "paired-user",
    )
    session.pair.side_effect = [failure, credentials]
    result = await session.submit(pin="12345678")
    assert_page(result, "error", "pin", "manual_ip")
    assert "fresh eight-digit PIN" in str(result.settings)
    assert "Link Device" in str(result.settings)
    assert session.flow._pending_device_config == pending
    assert first == original
    session.assert_unsaved()
    result = await session.submit(pin="87654321")
    assert isinstance(result, SetupComplete)
    assert session.pair.await_count == 2
    session.manager.add_or_update.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "values, message",
    [
        ({"pin": ""}, "eight-digit"),
        ({"pin": "1234567"}, "eight-digit"),
        ({"pin": "123456789"}, "eight-digit"),
        ({"pin": "abcd1234"}, "eight-digit"),
        ({"pin": "１２３４５６７８"}, "eight-digit"),
        ({"manual_ip": "", "pin": "12345678"}, "valid console IP"),
        ({"manual_ip": "not-an-ip", "pin": "12345678"}, "valid console IP"),
        ({"manual_ip": "192.0.2.999", "pin": "12345678"}, "valid console IP"),
    ],
)
async def test_invalid_pairing_input_returns_page_without_contacting_console(
    monkeypatch, values, message
):
    session = SetupSession(monkeypatch)
    await session.start("local_only")
    await session.submit(account_id="123")
    await session.submit(device_choice=HOST)
    result = await session.submit(**values)
    assert_page(result, "pin", "manual_ip", "error")
    assert message in str(result.settings)
    session.assert_unsaved()
    session.pair.assert_not_awaited()
    result = await session.submit(manual_ip=HOST, pin="12345678")
    assert isinstance(result, SetupComplete)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode, values",
    [
        ("psn_console", {"npsso": ""}),
        ("psn_console", {"npsso": "{broken"}),
        ("psn_console", {"npsso": '{"npsso": ["invalid"]}'}),
        ("psn_console", {"npsso": '{"npsso": 123}'}),
        ("local_only", {"account_id": ""}),
        ("local_only", {"account_id": "online-name"}),
        ("local_only", {"account_id": str(2**64)}),
    ],
)
async def test_invalid_credentials_return_account_page_then_allow_retry(
    monkeypatch, mode, values
):
    session = SetupSession(monkeypatch)
    await session.start(mode)
    result = await session.submit(**values)
    assert_page(result, "error", "account_id" if mode == "local_only" else "npsso")
    session.create_api.assert_not_called()
    session.assert_unsaved()
    result = await session.submit(
        **({"account_id": "123"} if mode == "local_only" else {"npsso": TOKEN})
    )
    assert_page(result, "device_choice")
    await session.submit(device_choice=HOST)
    result = await session.submit(pin="12345678")
    assert isinstance(result, SetupComplete)


@pytest.mark.asyncio
async def test_authentication_failure_and_wrong_account_return_page_without_changing_saved_device(
    monkeypatch,
):
    first = saved_console()
    original = copy.deepcopy(first)
    session = SetupSession(monkeypatch, [first], selected=first.identifier)
    await session.start("psn_console")
    session.api.get_user.side_effect = RuntimeError("expired token")
    result = await session.submit(npsso=NEW_TOKEN)
    assert_page(result, "error", "npsso")
    assert "Could not sign in" in str(result.settings)
    session.api.get_user.side_effect = None
    session.api.get_user.return_value.account_id = "456"
    result = await session.submit(npsso=NEW_TOKEN)
    assert_page(result, "error", "npsso")
    assert "different PSN account" in str(result.settings)
    session.assert_unsaved()
    assert first == original
    session.api.get_user.return_value.account_id = "123"
    result = await session.submit(npsso=NEW_TOKEN)
    assert isinstance(result, SetupComplete)


@pytest.mark.asyncio
async def test_failed_discovery_can_rescan_and_fall_back_to_manual_pairing(monkeypatch):
    session = SetupSession(monkeypatch)
    session.scan.side_effect = OSError("no broadcast")
    session.fallback_scan.side_effect = TimeoutError()
    await session.start("local_only")
    result = await session.submit(account_id="123")
    assert_page(result, "device_choice")
    assert "No consoles were found" in str(result.settings)
    result = await session.submit(device_choice="__rescan__")
    assert_page(result, "device_choice")
    assert session.scan.await_count == session.fallback_scan.await_count == 2
    result = await session.submit(device_choice="__manual__")
    assert_page(result, "manual_ip", "pin")
    session.assert_unsaved()
    result = await session.submit(manual_ip=HOST, pin="12345678")
    assert isinstance(result, SetupComplete)


@pytest.mark.asyncio
async def test_invalid_cloud_choice_stays_on_selection_page_until_valid(monkeypatch):
    session = SetupSession(monkeypatch)
    session.consoles.return_value = [{"duid": "one"}, {"duid": "two"}]
    await session.start("psn_console")
    await session.submit(npsso=TOKEN)
    await session.submit(device_choice=HOST)
    result = await session.submit(pin="12345678")
    assert_page(result, "cloud_console")
    for choice in ["", "unknown-console"]:
        result = await session.submit(cloud_console=choice)
        assert_page(result, "cloud_console")
        session.assert_unsaved()
    result = await session.submit(cloud_console="two")
    assert isinstance(result, SetupComplete)
    assert session.entries["console_112233445566"].launch_device_id == "two"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["psn_only", "local_only"])
async def test_existing_device_connection_modes_preserve_pairing_and_saved_ids(
    monkeypatch, mode
):
    first = saved_console()
    original = copy.deepcopy(first)
    session = SetupSession(monkeypatch, [first], selected=first.identifier)
    await session.start(mode)
    result = await session.submit(name="Renamed")
    assert isinstance(result, SetupComplete)
    updated = session.entries[first.identifier]
    assert updated.ps_device == original.ps_device
    assert updated.console_host_id == original.console_host_id
    assert updated.launch_device_id == original.launch_device_id
    assert updated.npsso == ("" if mode == "local_only" else TOKEN)
    session.scan.assert_not_awaited()
    session.pair.assert_not_awaited()
    session.consoles.assert_not_called()
    if mode == "local_only":
        session.create_api.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("profile_name", ["paired-user", "   ", "x" * 81])
async def test_invalid_additional_profile_returns_account_page_and_can_be_corrected(
    monkeypatch, profile_name
):
    first = saved_console()
    original = copy.deepcopy(first)
    session = SetupSession(monkeypatch, [first], selected=first.identifier)
    await session.start("psn_console")
    result = await session.submit(add_profile=True, profile_name=profile_name)
    assert_page(result, "error", "add_profile", "profile_name")
    session.pair.assert_not_awaited()
    session.assert_unsaved()
    assert first == original
    result = await session.submit(add_profile=True, profile_name="extra")
    assert_page(result, "pin")
    session.pair.return_value = pairing("aabbccddeeff", "extra")
    result = await session.submit(pin="12345678")
    assert isinstance(result, SetupComplete)
    assert set(session.entries[first.identifier].ps_device["profiles"]) == {
        "paired-user",
        "extra",
    }


@pytest.mark.asyncio
async def test_invalid_saved_account_choice_returns_account_page_and_can_be_corrected(
    monkeypatch,
):
    first = saved_console()
    session = SetupSession(monkeypatch, [first])
    await session.start("psn_console")
    result = await session.submit(psn_account="removed-account")
    assert_page(result, "error", "psn_account")
    session.create_api.assert_not_called()
    session.assert_unsaved()
    result = await session.submit(psn_account=first.identifier)
    assert_page(result, "device_choice")
    await session.submit(device_choice=HOST)
    result = await session.submit(pin="12345678")
    assert isinstance(result, SetupComplete)
    assert len(session.entries) == 2


@pytest.mark.asyncio
async def test_duplicate_console_returns_pairing_page_until_different_console_is_paired(
    monkeypatch,
):
    first = saved_console()
    original = copy.deepcopy(first)
    session = SetupSession(monkeypatch, [first])
    await session.start("psn_console")
    await session.submit(psn_account=first.identifier)
    await session.submit(device_choice=HOST)
    session.pair.side_effect = [pairing("aabbccddeeff"), pairing()]
    result = await session.submit(pin="12345678")
    assert_page(result, "error", "manual_ip", "pin")
    assert "already configured" in str(result.settings)
    session.assert_unsaved()
    assert first == original
    result = await session.submit(manual_ip="192.0.2.3", pin="87654321")
    assert isinstance(result, SetupComplete)
    assert len(session.entries) == 2
    assert first == original


@pytest.mark.asyncio
async def test_bad_token_rejected_during_client_construction_returns_account_page(
    monkeypatch,
):
    session = SetupSession(monkeypatch)
    await session.start("psn_only")
    session.create_api.side_effect = PSNAWPInvalidTokenError("bad token")
    result = await session.submit(npsso="short")
    assert_page(result, "error", "npsso")
    session.assert_unsaved()
    session.create_api.side_effect = None
    result = await session.submit(npsso=TOKEN)
    assert isinstance(result, SetupComplete)


@pytest.mark.asyncio
async def test_cloud_discovery_failure_does_not_block_local_pairing(monkeypatch):
    session = SetupSession(monkeypatch)
    session.consoles.side_effect = OSError("cloud unavailable")
    await session.start("psn_console")
    result = await session.submit(npsso=TOKEN)
    assert_page(result, "device_choice")
    await session.submit(device_choice=HOST)
    result = await session.submit(pin="12345678")
    assert isinstance(result, SetupComplete)
    assert session.entries["console_112233445566"].launch_device_id == ""


@pytest.mark.asyncio
async def test_only_cloud_console_is_saved_without_an_extra_selection_page(monkeypatch):
    session = SetupSession(monkeypatch)
    session.consoles.return_value = [{"duid": "only-cloud"}]
    await session.start("psn_console")
    await session.submit(npsso=TOKEN)
    await session.submit(device_choice=HOST)
    result = await session.submit(pin="12345678")
    assert isinstance(result, SetupComplete)
    assert session.entries["console_112233445566"].launch_device_id == "only-cloud"
