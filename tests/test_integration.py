"""Offline contracts for state, credential updates, entities and cloud requests."""

import asyncio
import copy
import time
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from api import PlayStationNetwork, PlayStationNetworkData
from cloud import (
    CloudAuthorizationError,
    CloudError,
    ConsoleSelectionError,
    SonyConsoleService,
)
from const import PSNConfig
from driver import PSNIntegrationDriver, entities_for_device
from media_player import PSNMediaPlayer
from playdirector import ConsoleError
from psn import PSNAccount
from psnawp_api.core.psnawp_exceptions import PSNAWPUnauthorizedError
from remote_control import PSNRemote
from selection import PSNSelect
from sensor import PSNDiagnosticSensor
from setup_flow import PSNSetupFlow
from switch import PSNSwitch
from ucapi import StatusCodes, media_player, remote, select, switch
from ucapi.api_definitions import Paging
from ucapi_framework import BaseIntegrationDriver


def attach(entity):
    """Model configured entity storage while exercising real framework updates."""
    entity._api = SimpleNamespace(
        configured_entities=SimpleNamespace(
            contains=lambda _: True,
            get=lambda _: entity,
            update_attributes=lambda _, values: entity.attributes.update(values),
        )
    )
    return entity


def config(**kwargs):
    return PSNConfig(
        "account",
        "PlayStation",
        npsso="secret",
        psn_account_id="123",
        ps_device={"device_ip": "192.0.2.1", "device_type": "PS5", "rp_key": "ab" * 16},
        **kwargs,
    )


class Response:
    status_code = 200

    def __init__(self, data):
        self.data = data

    def json(self):
        return self.data


class Auth:
    def __init__(self, inventory=None, status="completed"):
        self.calls = []
        self.inventory = inventory
        self.status = status

    def get(self, **kwargs):
        self.calls.append(("GET", kwargs))
        if kwargs["url"].endswith("clients"):
            row = {"duid": "console-duid", "device": {"name": "PlayStation"}}
            if self.inventory is not None:
                row["systemData"] = {"installedTitles": {"titles": self.inventory}}
            return Response({"clients": [row]})
        return Response(
            {"commands": [{"commandId": "command-1", "commandStatus": self.status}]}
        )

    def post(self, **kwargs):
        self.calls.append(("POST", kwargs))
        return Response({"commandId": "command-1"})


def test_cloud_launch_payload_and_confirmation():
    auth = Auth()
    SonyConsoleService(auth).launch("PPSA12345_00", "console-duid")
    assert [method for method, _ in auth.calls] == ["GET", "POST", "GET"]
    assert auth.calls[1][1]["json"] == {
        "commandDetail": {
            "platform": "PS5",
            "duid": "console-duid",
            "commandType": "launchTitle",
            "parameters": {"titleIds": ["PPSA12345"]},
        }
    }


def test_real_psnawp_unauthorized_exception_is_preserved():
    auth = SimpleNamespace(get=Mock(side_effect=PSNAWPUnauthorizedError("HTTP 401")))
    with pytest.raises(CloudAuthorizationError):
        SonyConsoleService(auth).inventory()


@pytest.mark.parametrize(
    "inventory,expected",
    [
        (None, None),
        ([], ()),
        (
            [
                {"npTitleId": "PPSA12345_00"},
                {"npTitleId": "PPSA12345"},
                {"npTitleId": "invalid"},
            ],
            ("PPSA12345",),
        ),
    ],
)
def test_unknown_empty_and_deduplicated_inventory(inventory, expected):
    assert SonyConsoleService(Auth(inventory)).inventory().titles == expected


def test_ambiguous_console_must_not_be_selected():
    rows = [{"duid": "one"}, {"duid": "two"}]
    with pytest.raises(ConsoleSelectionError):
        SonyConsoleService.select(rows)
    assert SonyConsoleService.select(rows, "two")["duid"] == "two"


@pytest.mark.parametrize("status", ["failed", "rejected", "cancelled"])
def test_launch_failure_is_not_success_or_retried(status):
    auth = Auth(status=status)
    with pytest.raises(CloudError):
        SonyConsoleService(auth).launch("PPSA12345", "console-duid")
    assert sum(method == "POST" for method, _ in auth.calls) == 1


@pytest.mark.asyncio
async def test_all_entities_keep_original_ids_and_add_subentity_ids():
    cfg = config()
    device = PSNAccount(cfg)
    entities = entities_for_device(cfg, device)
    ids = {entity.id for entity in entities}
    assert len(ids) == len(entities) == 14
    assert "select.account.library_view" not in ids
    assert "sensor.account.active_title_id" in ids
    assert {
        "media_player.account",
        "sensor.account",
        "sensor.account_authentication",
        "sensor.account.trophy_level",
        "select.account.profile",
        "remote.account",
        "switch.account",
    }.issubset(ids)
    for entity in entities:
        attach(entity)
        await entity.sync_state()
    assert (
        len([entity for entity in entities if isinstance(entity, PSNDiagnosticSensor)])
        == 8
    )


@pytest.mark.asyncio
async def test_psn_only_does_not_advertise_local_control():
    cfg = PSNConfig("account", "PSN", "secret")
    device = PSNAccount(cfg)
    entities = entities_for_device(cfg, device)
    assert not any(isinstance(entity, (PSNRemote, PSNSwitch)) for entity in entities)
    player = next(entity for entity in entities if isinstance(entity, PSNMediaPlayer))
    assert media_player.Features.ON_OFF not in player.features
    assert media_player.Features.PLAY_MEDIA not in player.features


@pytest.mark.asyncio
async def test_lan_power_survives_expired_psn_and_absent_console_is_unavailable():
    device = PSNAccount(config())
    device.psn_authenticated = False
    device._local_status = {"status-code": 200}
    device._publish()
    assert device.psn_state == media_player.States.ON
    entity = attach(PSNSwitch(device._device_config, device))
    device._local_status = {}
    device._publish()
    await entity.sync_state()
    assert device.psn_state == media_player.States.UNAVAILABLE
    assert entity.attributes[switch.Attributes.STATE] == switch.States.UNAVAILABLE


@pytest.mark.asyncio
async def test_switch_command_failures_are_reported():
    cfg = config()
    device = PSNAccount(cfg)
    device.power_on = AsyncMock(side_effect=ConsoleError("unreachable"))
    assert (
        await PSNSwitch(cfg, device).command(switch.Commands.ON, websocket=None)
        == StatusCodes.SERVICE_UNAVAILABLE
    )


@pytest.mark.asyncio
async def test_remote_validates_entire_sequence_before_input_and_forwards_hold():
    cfg = config()
    device = PSNAccount(cfg)
    device.send_button = AsyncMock()
    entity = PSNRemote(cfg, device)
    result = await entity.command(
        remote.Commands.SEND_CMD_SEQUENCE, {"sequence": ["CROSS", "INVALID"]}
    )
    assert result == StatusCodes.BAD_REQUEST
    device.send_button.assert_not_called()
    assert (
        await entity.command(
            remote.Commands.SEND_CMD, {"command": "CROSS", "hold": 250}
        )
        == StatusCodes.OK
    )
    device.send_button.assert_awaited_once_with("CROSS", hold=0.25)


@pytest.mark.asyncio
async def test_profile_selector_initially_empty_and_publishes_runtime_options():
    cfg = config()
    device = PSNAccount(cfg)
    entity = attach(PSNSelect(cfg, device))
    await entity.sync_state()
    assert entity.attributes[select.Attributes.STATE] == select.States.UNAVAILABLE
    device._control = SimpleNamespace(profiles=["one", "two"])
    device._device_config.ps_device["user"] = "one"
    device.select_profile = AsyncMock(return_value=True)
    await entity.sync_state()
    assert entity.attributes[select.Attributes.OPTIONS] == ["one", "two"]
    assert await entity.command(select.Commands.SELECT_NEXT) == StatusCodes.OK
    device.select_profile.assert_awaited_once_with("two")


@pytest.mark.asyncio
async def test_browser_pagination_and_playtime_come_from_stats():
    cfg = config()
    device = PSNAccount(cfg)
    device.psn_authenticated = True
    stats = SimpleNamespace(
        title_id="PPSA12345",
        name="Game",
        image_url="https://example.com/game.png",
        play_duration=timedelta(hours=12),
        play_count=2,
        first_played_date_time=datetime(2026, 1, 1, tzinfo=UTC),
        last_played_date_time=None,
    )
    device.get_game_library = AsyncMock(return_value=([stats], 7))
    device.get_game_details = AsyncMock(return_value=stats)
    player = PSNMediaPlayer(cfg, device)
    root = await player.browse(media_player.BrowseOptions(paging=Paging(limit=20)))
    assert [item.media_id for item in root.media.items] == [
        "psn_games",
        "installed_games",
    ]
    options = media_player.BrowseOptions(
        media_id="psn_games", paging=Paging(page=2, limit=3)
    )
    result = await player.browse(options)
    device.get_game_library.assert_awaited_once_with(3, 3)
    assert result.pagination.count == 7
    detail_id = result.media.items[0].media_id
    details = await player.browse(
        media_player.BrowseOptions(media_id=detail_id, paging=Paging(limit=20))
    )
    assert details.media.items[0].can_play
    assert "12.0 hours" in details.media.items[1].title


@pytest.mark.asyncio
async def test_update_token_preserves_pairing_identity_and_preferences(monkeypatch):
    cfg = config(launch_device_id="console-duid")
    saved = copy.deepcopy(cfg)
    flow = object.__new__(PSNSetupFlow)
    flow._selected_config_id = cfg.identifier
    flow.config = SimpleNamespace(get=lambda _: cfg)
    flow.driver = SimpleNamespace(loop=asyncio.get_running_loop())
    api = SimpleNamespace(
        get_user=lambda: SimpleNamespace(account_id="123", online_id="Player"),
        close=Mock(),
    )
    monkeypatch.setattr("setup_flow.PlayStationNetwork", lambda *args: api)
    monkeypatch.setattr("setup_flow.parse_npsso_token", lambda value: value)
    result = await flow.query_device({"npsso": "updated", "add_control": False})
    assert result.npsso == "updated"
    assert result.ps_device == saved.ps_device
    assert result.identifier == saved.identifier
    assert result.launch_device_id == "console-duid"
    assert cfg == saved


def test_config_repr_hides_credentials():
    assert "secret" not in repr(config())
    assert "rp_key" not in repr(config())


@pytest.mark.asyncio
async def test_authentication_does_not_become_valid_at_client_construction(monkeypatch):
    cfg = PSNConfig("account", "PSN", "secret")
    device = PSNAccount(cfg)
    fake = SimpleNamespace(close=Mock())
    monkeypatch.setattr("psn.PlayStationNetwork", lambda *args: fake)
    device.poll_device = AsyncMock()
    await device.establish_connection()
    assert device.psn_authenticated is None
    await device.disconnect()


def test_cached_history_page_uses_public_iterator_length():
    class Titles:
        def __iter__(self):
            return iter([SimpleNamespace(title_id="PPSA12345", name="Game")])

        def __len__(self):
            return 4

    api = object.__new__(PlayStationNetwork)
    api._pages, api._titles = {}, {}
    api._title_expiry = {}
    api.client = SimpleNamespace(
        title_stats=Mock(side_effect=lambda **kwargs: Titles())
    )
    assert api.get_game_page(10, 0)[1] == 4
    assert api.get_game_page(10, 0)[1] == 4
    assert api.client.title_stats.call_count == 1


def test_original_auth_sensor_maps_to_parent_without_renaming():
    fake = SimpleNamespace(
        entity_id_separator=".",
        get_device_config=lambda key: key == "account" and config() or None,
    )
    fake.device_from_entity_id = lambda value: (
        BaseIntegrationDriver.device_from_entity_id(fake, value)
    )
    # Run the actual subclass method with its real base method dispatch.
    driver = object.__new__(PSNIntegrationDriver)
    driver.entity_id_separator = "."
    driver.get_device_config = fake.get_device_config
    assert driver.device_from_entity_id("sensor.account_authentication") == "account"
    assert driver.device_from_entity_id("sensor.account.trophy_level") == "account"


@pytest.mark.asyncio
async def test_artwork_failure_preserves_known_inventory():
    from cloud import Inventory

    device = PSNAccount(config())
    device._psn = SimpleNamespace(
        psn=SimpleNamespace(authenticator=object()), get_title_metadata=Mock()
    )
    device._call = AsyncMock(
        side_effect=[
            Inventory("console-duid", "PlayStation", ("PPSA12345",)),
            RuntimeError("Artwork unavailable"),
        ]
    )
    await device._refresh_inventory()
    assert device.installed_count == 1
    assert device.installed_inventory_available
    games, total = await device.get_installed_game_library()
    assert total == 1 and games[0]["title_id"] == "PPSA12345"


@pytest.mark.asyncio
async def test_stale_presence_is_cleared_while_observed_console_remains_on():
    device = PSNAccount(config())
    device._local_status = {"status-code": 200}
    device._presence_time = time.monotonic() - 121
    device._presence_data = PlayStationNetworkData(
        {},
        "Player",
        "123",
        True,
        {"npTitleId": "PPSA12345", "titleName": "Stale game", "format": "PS5"},
        {"onlineStatus": "online", "platform": "PS5"},
        ["PS5"],
    )
    device._publish()
    assert device.psn_state == media_player.States.ON
    assert device.psn_media_title == ""


@pytest.mark.asyncio
async def test_failed_profile_persistence_restores_previous_controller_identity():
    cfg = config()
    device = PSNAccount(cfg, config_manager=SimpleNamespace(update=lambda _: False))
    account_id = "ewAAAAAAAAA="
    device._control = SimpleNamespace(
        profile="one",
        select_profile=AsyncMock(),
        credentials={
            "user": "one",
            "profiles": {"one": {"id": account_id}, "two": {"id": account_id}},
        },
    )
    assert not await device.select_profile("two")
    assert [
        call.args[0] for call in device._control.select_profile.await_args_list
    ] == ["two", "one"]


@pytest.mark.asyncio
async def test_account_requests_are_serialized_on_one_worker(monkeypatch):
    device = PSNAccount(PSNConfig("account", "PSN", "secret"))
    monkeypatch.setattr(
        "psn.PlayStationNetwork", lambda *args: SimpleNamespace(close=Mock())
    )
    device.poll_device = AsyncMock()
    await device.establish_connection()
    active, maximum = 0, 0

    def operation():
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        time.sleep(0.01)
        active -= 1

    await asyncio.gather(*(device._call(operation) for _ in range(4)))
    assert maximum == 1
    await device.disconnect()


@pytest.mark.asyncio
async def test_local_only_setup_discovers_then_pairs_without_cloud(monkeypatch):
    flow = object.__new__(PSNSetupFlow)
    flow._selected_config_id = None
    flow._selected_ip = ""
    flow._profile_name = "paired-user"
    flow._add_profile = False
    monkeypatch.setattr("setup_flow.scan", AsyncMock(return_value=[]))
    monkeypatch.setattr("setup_flow.async_search", AsyncMock(return_value=[]))
    credentials = {
        "device_ip": "192.0.2.1",
        "host_id": "aabbccddeeff",
        "device_type": "PS5",
        "host_name": "PS5",
        "user": "paired-user",
        "profiles": {"paired-user": {"id": "ewAAAAAAAAA=", "hosts": {}}},
    }
    pair = AsyncMock(return_value=credentials)
    monkeypatch.setattr("setup_flow.pair_console", pair)
    cloud = Mock(side_effect=AssertionError("Cloud authentication must not be used"))
    monkeypatch.setattr("setup_flow.PlayStationNetwork", cloud)
    await flow.query_device({"setup_mode": "local_only"})
    await flow.query_device({"account_id": "123", "add_control": True})
    assert flow._stage == "select"
    await flow.handle_additional_configuration_response(
        SimpleNamespace(input_values={"device_choice": "__manual__"})
    )
    result = await flow.handle_additional_configuration_response(
        SimpleNamespace(input_values={"manual_ip": "192.0.2.1", "pin": "12345678"})
    )
    assert result.identifier == "console_aabbccddeeff"
    assert result.npsso == ""
    pair.assert_awaited_once_with("192.0.2.1", "123", "12345678", "paired-user")


@pytest.mark.asyncio
async def test_auth_sensor_validates_before_expensive_presence_work():
    device = PSNAccount(config())
    data = PlayStationNetworkData({}, "Player", "123", False, {}, {}, ["PS5"])
    user = SimpleNamespace(account_id="123")
    api = SimpleNamespace(get_user=Mock(), get_data=Mock())
    device._psn = api

    async def call(function, *args):
        if function is api.get_data:
            assert device.psn_authenticated is True
            return data
        return user

    device._call = call
    await device._refresh_presence()
    assert device.psn_authenticated is True


@pytest.mark.asyncio
async def test_malformed_token_json_returns_form_without_request():
    from ucapi import RequestUserInput

    flow = object.__new__(PSNSetupFlow)
    flow._selected_config_id = None
    result = await flow.query_device({"npsso": "{broken"})
    assert isinstance(result, RequestUserInput)


def test_psn_clients_share_budget_and_stop_before_sending_when_exhausted(monkeypatch):
    from api import PSNRequestLimiter
    from psnawp_api.core.psnawp_exceptions import PSNAWPTooManyRequestsError
    from pyrate_limiter import Duration, Rate
    from requests import Response as HTTPResponse

    limiter = PSNRequestLimiter(Rate(2, Duration.MINUTE * 15))
    monkeypatch.setattr("api._PSN_REQUEST_LIMITER", limiter)
    clock = [1_000_000]
    monkeypatch.setattr(limiter.buckets()[0], "now", lambda: clock[0])
    clients = [PlayStationNetwork("unused", None) for _ in range(2)]
    builders = [client.psn.authenticator.request_builder for client in clients]
    response = HTTPResponse()
    response.status_code = 200
    response._content = b"{}"
    transport = Mock(return_value=response)
    monkeypatch.setattr("api.HTTPAdapter.send", transport)
    # Retain PSNAWP's independent limiter and its original background lifecycle.
    assert all(builder.limiter is not limiter for builder in builders)
    try:
        builders[0].get(url="https://example.invalid/presence")
        builders[1].post(url="https://example.invalid/commands")
        with pytest.raises(PSNAWPTooManyRequestsError):
            builders[0].get(url="https://example.invalid/trophies")
        assert transport.call_count == 2
        clock[0] += int(Duration.MINUTE * 15) + 1
        builders[0].get(url="https://example.invalid/presence")
        assert transport.call_count == 3
    finally:
        for client in clients:
            client.close()
        limiter.dispose(limiter.buckets()[0])


def test_inventory_metadata_caches_missing_titles_as_well_as_matches(monkeypatch):
    api = object.__new__(PlayStationNetwork)
    api._metadata, api._titles = {}, {}
    api.get_game_page = Mock(return_value=([], 0))
    api.client = SimpleNamespace(trophy_titles_for_title=Mock(return_value=[]))
    clock = [1000]
    monkeypatch.setattr("api.time.monotonic", lambda: clock[0])
    expected = {"PPSA12345": {"name": "PPSA12345", "image": ""}}
    assert api.get_title_metadata(["PPSA12345"]) == expected
    clock[0] += 900
    assert api.get_title_metadata(["PPSA12345"]) == expected
    assert api.client.trophy_titles_for_title.call_count == 1
    clock[0] += 3600
    api.get_title_metadata(["PPSA12345"])
    assert api.client.trophy_titles_for_title.call_count == 2


@pytest.mark.asyncio
async def test_title_progress_timer_is_not_reset_while_a_refresh_is_pending():
    device = PSNAccount(config())
    device._local_status = {
        "status-code": 200,
        "running-app-titleid": "PPSA12345",
    }
    device._publish()
    assert device._next_progress == 0
    device._next_progress = time.monotonic() + 300
    device._publish()
    assert device._next_progress > time.monotonic()
    device._local_status["running-app-titleid"] = "PPSA54321"
    device._publish()
    assert device._next_progress == 0


def new_setup_flow():
    flow = object.__new__(PSNSetupFlow)
    flow._selected_config_id = None
    flow._selected_ip = ""
    flow.driver = SimpleNamespace(loop=asyncio.get_running_loop())
    return flow


@pytest.mark.asyncio
async def test_setup_separates_psn_links_from_advanced_local_account_entry(monkeypatch):
    flow = new_setup_flow()
    welcome = flow.get_manual_entry_form()
    assert {row["id"] for row in welcome.settings} == {"information", "setup_mode"}
    token_form = await flow.query_device({"setup_mode": "psn_console"})
    assert "https://www.playstation.com/" in str(token_form.settings)
    assert "https://ca.account.sony.com/api/v1/ssocookie" in str(token_form.settings)
    assert not {"account_id", "launch_device_id", "disable_psn"} & {
        row["id"] for row in token_form.settings
    }
    # Error messages must preserve the links needed to recover.
    assert "ssocookie" in str(flow._form("Invalid token").settings)
    local_form = await flow.query_device({"setup_mode": "local_only"})
    assert "account_id" in {row["id"] for row in local_form.settings}
    assert "npsso" not in {row["id"] for row in local_form.settings}


@pytest.mark.asyncio
async def test_recommended_setup_gets_account_id_and_discovers_console(monkeypatch):
    flow = new_setup_flow()
    await flow.query_device({"setup_mode": "psn_console"})
    api = SimpleNamespace(
        get_user=lambda: SimpleNamespace(account_id="123", online_id="Player"),
        close=Mock(),
        psn=SimpleNamespace(authenticator=Auth()),
    )
    monkeypatch.setattr("setup_flow.PlayStationNetwork", lambda *args: api)
    monkeypatch.setattr("setup_flow.parse_npsso_token", lambda value: value)
    search = AsyncMock(
        return_value=[
            {"host-ip": "192.0.2.1", "host-name": "Living Room", "host-type": "PS5"}
        ]
    )
    monkeypatch.setattr("setup_flow.scan", search)
    result = await flow.query_device({"npsso": "token"})
    search.assert_awaited_once_with(timeout=5)
    assert flow._pending_device_config.psn_account_id == "123"
    assert "Living Room" in str(result.settings)
    assert flow._stage == "select"
    paired = config()
    paired.launch_device_id = ""
    completed = flow._finish_console(paired)
    assert completed.launch_device_id == "console-duid"


@pytest.mark.asyncio
async def test_psn_only_setup_skips_local_and_cloud_discovery(monkeypatch):
    flow = new_setup_flow()
    await flow.query_device({"setup_mode": "psn_only"})
    api = SimpleNamespace(
        get_user=lambda: SimpleNamespace(account_id="123", online_id="Player"),
        close=Mock(),
    )
    monkeypatch.setattr("setup_flow.PlayStationNetwork", lambda *args: api)
    monkeypatch.setattr("setup_flow.parse_npsso_token", lambda value: value)
    search = AsyncMock(
        side_effect=AssertionError("PSN-only must not discover consoles")
    )
    monkeypatch.setattr("setup_flow.scan", search)
    cloud = Mock(
        side_effect=AssertionError("PSN-only must not discover cloud consoles")
    )
    monkeypatch.setattr("setup_flow.SonyConsoleService", cloud)
    result = await flow.query_device({"npsso": "token"})
    assert result.psn_account_id == "123"
    assert result.ps_device == {}
    search.assert_not_called()
    cloud.assert_not_called()


@pytest.mark.asyncio
async def test_discovery_falls_back_to_interfaces_and_can_retry(monkeypatch):
    flow = new_setup_flow()
    first = AsyncMock(side_effect=RuntimeError("No broadcast socket"))
    second = AsyncMock(return_value=[])
    monkeypatch.setattr("setup_flow.scan", first)
    monkeypatch.setattr("setup_flow.async_search", second)
    result = await flow._discover_consoles()
    second.assert_awaited_once_with(timeout=5, directed=True)
    assert "No consoles were found" in str(result.settings)
    assert "Enable Remote Play" in str(result.settings)
    await flow.handle_additional_configuration_response(
        SimpleNamespace(input_values={"device_choice": "__rescan__"})
    )
    assert second.await_count == 2


@pytest.mark.asyncio
async def test_ambiguous_cloud_console_uses_named_selection_without_id_entry():
    flow = new_setup_flow()
    flow._cloud_consoles = [
        {"duid": "first-id", "device": {"name": "Living Room"}},
        {"duid": "second-id", "device": {"name": "Office"}},
    ]
    cfg = config()
    cfg.console_name = "Living Room"
    result = flow._finish_console(cfg)
    assert flow._stage == "cloud_select"
    assert "Office" in str(result.settings)
    completed = await flow.handle_additional_configuration_response(
        SimpleNamespace(input_values={"cloud_console": "second-id"})
    )
    assert completed.launch_device_id == "second-id"
    assert cfg.launch_device_id == ""
    for row in flow._cloud_consoles:
        row["device"]["name"] = "PS5"
    result = flow._finish_console(cfg)
    dropdown = next(row for row in result.settings if row["id"] == "cloud_console")
    assert dropdown["field"]["dropdown"]["value"] == "first-id"
    assert {item["id"] for item in dropdown["field"]["dropdown"]["items"]} == {
        "first-id",
        "second-id",
        "__skip__",
    }
    skipped = await flow.handle_additional_configuration_response(
        SimpleNamespace(input_values={"cloud_console": "__skip__"})
    )
    assert skipped is cfg
    assert skipped.launch_device_id == ""


def test_discovered_consoles_show_ip_state_and_reported_model():
    from setup_flow import _local_console_label

    regular = _local_console_label(
        {
            "host-name": "PS5",
            "host-type": "PS5",
            "host-ip": "192.0.2.10",
            "status-code": 620,
        }
    )
    pro = _local_console_label(
        {
            "host-name": "PS5",
            "host-type": "PS5",
            "host-ip": "192.0.2.20",
            "host-model": "PS5 Pro",
            "status-code": 200,
            "running-app-name": "Astro Bot",
        }
    )
    assert "192.0.2.10" in regular and "Rest mode" in regular
    assert "Pro" not in regular
    assert "192.0.2.20" in pro and "PS5 Pro" in pro
    assert "On" in pro and "Astro Bot" in pro


def test_cloud_labels_show_names_and_reported_models_without_internal_ids():
    from setup_flow import _cloud_console_label

    assert (
        _cloud_console_label({"duid": "first-12345678", "device": {"name": "PS5"}})
        == "PS5"
    )
    assert (
        _cloud_console_label(
            {
                "duid": "second-87654321",
                "device": {"name": "PS5", "modelName": "PS5 Pro"},
            }
        )
        == "PS5 — PS5 Pro"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "game_platform,title_id", [("PS4", "CUSA01780"), ("PS5", "PPSA12345")]
)
@pytest.mark.parametrize("local_title", [False, True])
async def test_now_playing_preserves_game_platform_and_artwork_on_ps5(
    game_platform, title_id, local_title
):
    device = PSNAccount(config())
    device._local_status = {"status-code": 200}
    if local_title:
        device._local_status["running-app-titleid"] = title_id.lower()
    metadata = {
        "npTitleId": title_id + "_00",
        "titleName": "Current title",
        "format": game_platform,
        "npTitleIconUrl": "https://example.com/current.png",
    }
    saved = dict(metadata)
    device._presence_time = time.monotonic()
    device._presence_data = PlayStationNetworkData(
        {},
        "Player",
        "123",
        True,
        metadata,
        {"onlineStatus": "online", "platform": "PS5"},
        ["PS5"],
    )
    device._publish()
    assert device.psn_state == media_player.States.PLAYING
    assert device.psn_media_title == "Current title"
    assert device.psn_media_artist == game_platform
    assert device.psn_media_image_url == "https://example.com/current.png"
    assert metadata == saved
    player = attach(PSNMediaPlayer(device._device_config, device))
    await player.sync_state()
    assert player.attributes[media_player.Attributes.MEDIA_TITLE] == "Current title"
    assert player.attributes[media_player.Attributes.MEDIA_ARTIST] == game_platform
    assert (
        player.attributes[media_player.Attributes.MEDIA_IMAGE_URL]
        == "https://example.com/current.png"
    )


@pytest.mark.asyncio
async def test_local_title_uses_matching_cached_artwork_without_stale_cloud_title():
    device = PSNAccount(config())
    device._local_status = {"status-code": 200, "running-app-titleid": "cusa12345_00"}
    device._installed_metadata = {
        "CUSA12345": {
            "name": "Installed PS4 game",
            "image": "https://example.com/installed.png",
        }
    }
    device._presence_time = time.monotonic()
    device._presence_data = PlayStationNetworkData(
        {},
        "Player",
        "123",
        True,
        {
            "npTitleId": "PPSA54321",
            "titleName": "Old game",
            "format": "PS5",
            "conceptIconUrl": "https://example.com/old.png",
        },
        {"onlineStatus": "online", "platform": "PS5"},
        ["PS5"],
    )
    device._publish()
    assert device.psn_media_title == "Installed PS4 game"
    assert device.psn_media_artist == "PS4"
    assert device.psn_media_image_url == "https://example.com/installed.png"
    device._local_status = {"status-code": 620}
    device._publish()
    assert device.psn_state == media_player.States.OFF
    assert (
        device.psn_media_title
        == device.psn_media_artist
        == device.psn_media_image_url
        == ""
    )


@pytest.mark.asyncio
async def test_presence_from_another_console_platform_does_not_set_local_game():
    device = PSNAccount(config())
    device._local_status = {"status-code": 200}
    device._presence_time = time.monotonic()
    device._presence_data = PlayStationNetworkData(
        {},
        "Player",
        "123",
        True,
        {"npTitleId": "CUSA12345", "titleName": "Other console", "format": "PS4"},
        {"onlineStatus": "online", "platform": "PS4"},
        ["PS4", "PS5"],
    )
    device._publish()
    assert device.psn_state == media_player.States.ON
    assert (
        device.psn_media_title
        == device.psn_media_artist
        == device.psn_media_image_url
        == ""
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("duplicate_console", [False, True])
async def test_add_second_ps5_reuses_account_and_keeps_first_console_independent(
    monkeypatch, duplicate_console
):
    from ucapi import RequestUserInput, SetupComplete
    from ucapi_framework.setup import SetupSteps

    first = config(launch_device_id="first-cloud")
    first.name = "PS5"
    first.console_host_id = "aa:bb:cc:dd:ee:ff"
    first.ps_device.update(host_id="aabbccddeeff", host_name="PS5")
    saved_first = copy.deepcopy(first)
    entries = {first.identifier: first}
    manager = SimpleNamespace(
        all=lambda: list(entries.values()),
        get=entries.get,
        contains=lambda identifier: identifier in entries,
        add_or_update=lambda value: entries.__setitem__(value.identifier, value),
        remove=Mock(),
    )
    flow = PSNSetupFlow(
        manager, driver=SimpleNamespace(loop=asyncio.get_running_loop())
    )
    flow._setup_step = SetupSteps.MANUAL_ENTRY
    flow._add_mode = True
    flow._await_setup_completion = AsyncMock()
    flow.get_manual_entry_form()
    account_form = await flow._handle_user_data_response(
        SimpleNamespace(input_values={"setup_mode": "psn_console"})
    )
    assert "psn_account" in {row["id"] for row in account_form.settings}
    cloud_rows = [
        {"duid": "first-cloud", "device": {"name": "PS5"}},
        {"duid": "second-cloud", "device": {"name": "PS5 Pro"}},
    ]
    api = SimpleNamespace(
        get_user=lambda: SimpleNamespace(account_id="123", online_id="Player"),
        psn=SimpleNamespace(authenticator=object()),
        close=Mock(),
    )
    create_api = Mock(return_value=api)
    monkeypatch.setattr("setup_flow.PlayStationNetwork", create_api)
    monkeypatch.setattr(
        "setup_flow.SonyConsoleService",
        lambda auth: SimpleNamespace(consoles=lambda: cloud_rows),
    )
    monkeypatch.setattr(
        "setup_flow.scan",
        AsyncMock(
            return_value=[
                {"host-ip": "192.0.2.2", "host-name": "PS5", "host-type": "PS5"}
            ]
        ),
    )
    await flow._handle_user_data_response(
        SimpleNamespace(input_values={"psn_account": first.identifier, "npsso": ""})
    )
    create_api.assert_called_once_with(first.npsso, flow.driver.loop)
    assert flow._pending_device_config.ps_device == {}
    assert flow._pending_device_config.launch_device_id == ""
    await flow._handle_user_data_response(
        SimpleNamespace(input_values={"device_choice": "192.0.2.2"})
    )
    new_host_id = "aabbccddeeff" if duplicate_console else "112233445566"
    credentials = {
        "device_ip": "192.0.2.2",
        "device_type": "PS5",
        "host_id": new_host_id,
        "host_name": "PS5",
        "user": "paired-user",
        "profiles": {},
    }
    monkeypatch.setattr("setup_flow.pair_console", AsyncMock(return_value=credentials))
    result = await flow._handle_user_data_response(
        SimpleNamespace(input_values={"pin": "12345678"})
    )
    assert first == saved_first
    if duplicate_console:
        assert isinstance(result, RequestUserInput)
        assert "already configured" in str(result.settings)
        assert len(entries) == 1
    else:
        assert flow._stage == "cloud_select"
        assert len(entries) == 1
        result = await flow._handle_user_data_response(
            SimpleNamespace(input_values={"cloud_console": "second-cloud"})
        )
        assert isinstance(result, SetupComplete)
        assert len(entries) == 2
        second = entries["console_112233445566"]
        assert second.ps_device == credentials
        assert second.launch_device_id == "second-cloud"
        assert second.psn_account_id == first.psn_account_id
        assert second.name == "PS5 (192.0.2.2)"
        assert first == saved_first
    manager.remove.assert_not_called()


@pytest.mark.asyncio
async def test_pairing_retry_can_correct_discovered_console_ip(monkeypatch):
    flow = new_setup_flow()
    flow._pending_device_config = PSNConfig("", "PS5", psn_account_id="123")
    flow._stage = "pin"
    flow._selected_ip = "192.0.2.1"
    flow._profile_name = "paired-user"
    flow._add_profile = False
    credentials = {
        "device_ip": "192.0.2.2",
        "device_type": "PS5",
        "host_id": "112233445566",
    }
    pair = AsyncMock(side_effect=[OSError("unreachable"), credentials])
    monkeypatch.setattr("setup_flow.pair_console", pair)
    result = await flow.handle_additional_configuration_response(
        SimpleNamespace(input_values={"pin": "12345678"})
    )
    ip_field = next(row for row in result.settings if row["id"] == "manual_ip")
    assert ip_field["field"]["text"]["value"] == "192.0.2.1"
    result = await flow.handle_additional_configuration_response(
        SimpleNamespace(input_values={"manual_ip": "192.0.2.2", "pin": "87654321"})
    )
    pair.assert_awaited_with("192.0.2.2", "123", "87654321", "paired-user")
    assert result.ps_device == credentials


@pytest.mark.asyncio
@pytest.mark.parametrize("has_local_identity", [False, True])
async def test_update_cannot_pair_a_different_or_already_configured_console(
    monkeypatch, has_local_identity
):
    flow = new_setup_flow()
    selected = PSNConfig("selected", "Original", psn_account_id="123")
    if has_local_identity:
        selected.console_host_id = "aa:bb:cc:dd:ee:ff"
    other = PSNConfig("other", "Other", console_host_id="11:22:33:44:55:66")
    entries = {item.identifier: item for item in [selected, other]}
    flow.config = SimpleNamespace(get=entries.get, all=lambda: list(entries.values()))
    flow._selected_config_id = selected.identifier
    flow._pending_device_config = copy.deepcopy(selected)
    flow._stage = "pin"
    flow._profile_name = "paired-user"
    flow._add_profile = False
    monkeypatch.setattr(
        "setup_flow.pair_console",
        AsyncMock(return_value={"host_id": "112233445566"}),
    )
    result = await flow.handle_additional_configuration_response(
        SimpleNamespace(input_values={"manual_ip": "192.0.2.2", "pin": "12345678"})
    )
    expected = (
        "differs from the saved console" if has_local_identity else "already configured"
    )
    assert expected in str(result.settings)
    assert flow._pending_device_config == selected


@pytest.mark.asyncio
async def test_existing_psn_only_account_can_enable_controls_using_connection_mode(
    monkeypatch,
):
    flow = new_setup_flow()
    selected = PSNConfig("123", "Player", "secret", psn_account_id="123")
    flow.config = SimpleNamespace(get=lambda _: selected)
    flow._selected_config_id = selected.identifier
    form = await flow.query_device({"setup_mode": "psn_console"})
    assert "add_control" not in {row["id"] for row in form.settings}
    api = SimpleNamespace(
        get_user=lambda: SimpleNamespace(account_id="123", online_id="Player"),
        psn=SimpleNamespace(authenticator=Auth()),
        close=Mock(),
    )
    monkeypatch.setattr("setup_flow.PlayStationNetwork", lambda *args: api)
    search = AsyncMock(return_value=[])
    monkeypatch.setattr("setup_flow.scan", search)
    monkeypatch.setattr("setup_flow.async_search", AsyncMock(return_value=[]))
    await flow.query_device({"npsso": ""})
    search.assert_awaited_once()
    assert flow._stage == "select"
    assert selected.ps_device == {}


@pytest.mark.asyncio
async def test_existing_console_can_change_cloud_selection_without_repairing(
    monkeypatch,
):
    flow = new_setup_flow()
    selected = config(launch_device_id="old-cloud")
    original = copy.deepcopy(selected)
    flow.config = SimpleNamespace(get=lambda _: selected)
    flow._selected_config_id = selected.identifier
    form = await flow.query_device({"setup_mode": "psn_console"})
    assert "change_cloud_console" in {row["id"] for row in form.settings}
    api = SimpleNamespace(
        get_user=lambda: SimpleNamespace(account_id="123", online_id="Player"),
        psn=SimpleNamespace(authenticator=object()),
        close=Mock(),
    )
    monkeypatch.setattr("setup_flow.PlayStationNetwork", lambda *args: api)
    monkeypatch.setattr(
        "setup_flow.SonyConsoleService",
        lambda auth: SimpleNamespace(
            consoles=lambda: [
                {"duid": "old-cloud", "device": {"name": "Living Room"}},
                {"duid": "new-cloud", "device": {"name": "Office"}},
            ]
        ),
    )
    pair = AsyncMock(side_effect=AssertionError("Pairing must be preserved"))
    monkeypatch.setattr("setup_flow.pair_console", pair)
    await flow.query_device({"change_cloud_console": True})
    assert flow._stage == "cloud_select"
    result = await flow.handle_additional_configuration_response(
        SimpleNamespace(input_values={"cloud_console": "new-cloud"})
    )
    assert result.launch_device_id == "new-cloud"
    assert result.ps_device == selected.ps_device
    assert selected == original
    pair.assert_not_awaited()


@pytest.mark.asyncio
async def test_second_console_does_not_automatically_reuse_first_cloud_selection():
    flow = new_setup_flow()
    first = config(launch_device_id="first-cloud")
    flow.config = SimpleNamespace(all=lambda: [first])
    flow._cloud_consoles = [{"duid": "first-cloud", "device": {"name": "PS5"}}]
    second = config()
    second.identifier = "second"
    result = flow._finish_console(second)
    assert flow._stage == "cloud_select"
    assert "configured for PlayStation" in str(result.settings)
    assert second.launch_device_id == ""
