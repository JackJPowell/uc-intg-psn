#!/usr/bin/env python3
"""
This module implements a Remote Two integration driver for PlayStation Network Activity.

:copyright: (c) 2023-2024 by Unfolded Circle ApS.
:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import asyncio
import logging
import os
from typing import Any

import config
import psn
import setup_flow
import ucapi
import ucapi.api as uc
from psn import PSNAccount
from ucapi import MediaPlayer, StatusCodes, media_player

_LOG = logging.getLogger("driver")  # avoid having __main__ in log messages
_LOOP = asyncio.get_event_loop()

# Global variables
api = uc.IntegrationAPI(_LOOP)
_configured_accounts: dict[str, psn.PSNAccount] = {}


@api.listens_to(ucapi.Events.CONNECT)
async def on_r2_connect_cmd() -> None:
    """Connect all configured Accounts when the Remote Two sends the connect command."""
    _LOG.debug("Client connect command: connecting device(s)")
    await api.set_device_state(
        ucapi.DeviceStates.CONNECTED
    )  # just to make sure the device state is set
    for account in _configured_accounts.values():
        # start background task
        await account.connect()


@api.listens_to(ucapi.Events.DISCONNECT)
async def on_r2_disconnect_cmd():
    """Disconnect all configured Accounts when the Remote Two sends the disconnect command."""
    _LOG.debug("Client disconnect command: disconnecting device(s)")
    for account in _configured_accounts.values():
        await account.disconnect()


@api.listens_to(ucapi.Events.ENTER_STANDBY)
async def on_r2_enter_standby() -> None:
    """
    Enter standby notification from Remote Two.

    Disconnect every PSN Account instances.
    """
    _LOG.debug("Enter standby event: disconnecting device(s)")
    for account in _configured_accounts.values():
        await account.disconnect()


@api.listens_to(ucapi.Events.EXIT_STANDBY)
async def on_r2_exit_standby() -> None:
    """
    Exit standby notification from Remote Two.

    Connect all PSN Account instances.
    """
    _LOG.debug("Exit standby event: connecting account(s)")
    for account in _configured_accounts.values():
        await account.connect()


@api.listens_to(ucapi.Events.SUBSCRIBE_ENTITIES)
async def on_subscribe_entities(entity_ids: list[str]) -> None:
    """
    Subscribe to given entities.

    :param entity_ids: entity identifiers.
    """
    _LOG.debug("Subscribe entities event: %s", entity_ids)
    for entity_id in entity_ids:
        psn_id = entity_id
        if psn_id in _configured_accounts:
            playstation = _configured_accounts[psn_id]
            _LOG.info("Add '%s' to configured devices and connect", playstation.name)
            if playstation.is_on is None:
                state = media_player.States.UNAVAILABLE
            else:
                # TODO Improve State
                state = (
                    media_player.States.ON
                    if playstation.is_on
                    else media_player.States.OFF
                )
            api.configured_entities.update_attributes(
                entity_id, {media_player.Attributes.STATE: state}
            )
            await playstation.connect()
            continue

        device = config.devices.get(psn_id)
        if device:
            _add_configured_psn(device)
        else:
            _LOG.error(
                "Failed to subscribe entity %s: no PSN instance found", entity_id
            )


@api.listens_to(ucapi.Events.UNSUBSCRIBE_ENTITIES)
async def on_unsubscribe_entities(entity_ids: list[str]) -> None:
    """On unsubscribe, we disconnect the objects and remove listeners for events."""
    _LOG.debug("Unsubscribe entities event: %s", entity_ids)
    for entity_id in entity_ids:
        if entity_id in _configured_accounts:
            account = _configured_accounts.pop(entity_id)
            _LOG.info(
                "Removed '%s' from configured accounts and disconnect", account.name
            )
            await account.disconnect()
            account.events.remove_all_listeners()


# pylint: disable=too-many-statements
async def media_player_cmd_handler(
    entity: MediaPlayer, cmd_id: str, params: dict[str, Any] | None
) -> ucapi.StatusCodes:
    """Handle media player events."""
    _LOG.info(
        "Got %s command request: %s %s", entity.id, cmd_id, params if params else ""
    )

    return StatusCodes.OK


async def on_psn_connected(identifier: str) -> None:
    """Handle PSN connection."""
    _LOG.debug("PSN connected: %s", identifier)
    state = media_player.States.UNKNOWN
    if identifier in _configured_accounts:
        account = _configured_accounts[identifier]
        if account_state := account.state:
            state = _psn_state_to_media_player_state(account_state)

    api.configured_entities.update_attributes(
        identifier, {media_player.Attributes.STATE: state}
    )
    await api.set_device_state(
        ucapi.DeviceStates.CONNECTED
    )  # just to make sure the device state is set


async def on_psn_disconnected(identifier: str) -> None:
    """Handle PSN disconnection."""
    _LOG.debug("PSN disconnected: %s", identifier)
    api.configured_entities.update_attributes(
        identifier, {media_player.Attributes.STATE: media_player.States.OFF}
    )


async def on_psn_connection_error(identifier: str, message) -> None:
    """Set entities of PSN to state UNAVAILABLE if PSN connection error occurred."""
    _LOG.error(message)
    api.configured_entities.update_attributes(
        identifier, {media_player.Attributes.STATE: media_player.States.UNAVAILABLE}
    )
    await api.set_device_state(ucapi.DeviceStates.ERROR)


def _psn_state_to_media_player_state(
    device_state: str,
) -> media_player.States:
    match device_state:
        case "ON":
            state = media_player.States.ON
        case "OFF":
            state = media_player.States.OFF
        case "MENU":
            state = media_player.States.ON
        case "PLAYING":
            state = media_player.States.PLAYING
        case _:
            state = media_player.States.UNKNOWN
    return state


# pylint: disable=too-many-branches,too-many-statements
async def on_psn_update(entity_id: str, update: dict[str, Any] | None) -> None:
    """
    Update attributes of configured media-player entity if PSN properties changed.

    :param entity_id: PSN media-player entity identifier
    :param update: dictionary containing the updated properties or None
    """
    attributes = {}

    if api.configured_entities.contains(entity_id):
        target_entity = api.configured_entities.get(entity_id)
    else:
        target_entity = api.available_entities.get(entity_id)
    if target_entity is None:
        return

    if "state" in update:
        state = _psn_state_to_media_player_state(update["state"])
        if target_entity.attributes.get(media_player.Attributes.STATE, None) != state:
            attributes[media_player.Attributes.STATE] = state

    if "artwork" in update:
        attributes[media_player.Attributes.MEDIA_IMAGE_URL] = update["artwork"]
    if "title" in update:
        attributes[media_player.Attributes.MEDIA_TITLE] = update["title"]
    if "artist" in update:
        attributes[media_player.Attributes.MEDIA_ARTIST] = update["artist"]

    if media_player.Attributes.STATE in attributes:
        if attributes[media_player.Attributes.STATE] == media_player.States.OFF:
            attributes[media_player.Attributes.MEDIA_IMAGE_URL] = ""
            attributes[media_player.Attributes.MEDIA_ARTIST] = ""
            attributes[media_player.Attributes.MEDIA_TITLE] = ""
            attributes[media_player.Attributes.MEDIA_DURATION] = 0

    if attributes:
        if api.configured_entities.contains(entity_id):
            api.configured_entities.update_attributes(entity_id, attributes)
        else:
            api.available_entities.update_attributes(entity_id, attributes)


def _add_configured_psn(device: config.PSNDevice) -> None:
    # the device should not yet be configured, but better be safe
    if device.identifier in _configured_accounts:
        playstation = _configured_accounts[device.identifier]
        _LOOP.create_task(playstation.disconnect())
    else:
        _LOG.debug(
            "Adding new PSN device: %s (%s)",
            device.name,
            device.identifier,
        )
        playstation = PSNAccount(device, loop=_LOOP)
        playstation.events.on(psn.EVENTS.CONNECTED, on_psn_connected)
        playstation.events.on(psn.EVENTS.DISCONNECTED, on_psn_disconnected)
        playstation.events.on(psn.EVENTS.ERROR, on_psn_connection_error)
        playstation.events.on(psn.EVENTS.UPDATE, on_psn_update)

        _configured_accounts[device.identifier] = playstation

    _register_available_entities(device.identifier, device.name)


def _register_available_entities(identifier: str, name: str) -> bool:
    """
    Add a new PSN device to the available entities.

    :param identifier: PSN identifier
    :param name: Friendly name
    :return: True if added, False if the device was already in storage.
    """
    entity_id = identifier
    features = [
        media_player.Features.MEDIA_TITLE,
        media_player.Features.MEDIA_ARTIST,
        media_player.Features.MEDIA_IMAGE_URL,
    ]

    entity = MediaPlayer(
        entity_id,
        name,
        features,
        {
            media_player.Attributes.STATE: media_player.States.UNAVAILABLE,
            media_player.Attributes.MEDIA_IMAGE_URL: "",
            media_player.Attributes.MEDIA_TITLE: "",
            media_player.Attributes.MEDIA_ARTIST: "",
        },
        device_class=media_player.DeviceClasses.TV,
        options={},
        cmd_handler=media_player_cmd_handler,
    )

    if api.available_entities.contains(entity.id):
        api.available_entities.remove(entity.id)
    return api.available_entities.add(entity)


def on_device_added(device: config.PSNDevice) -> None:
    """Handle a newly added device in the configuration."""
    _LOG.debug("New device added: %s", device)
    _add_configured_psn(device)


def on_device_removed(device: config.PSNDevice | None) -> None:
    """Handle a removed device in the configuration."""
    if device is None:
        _LOG.debug(
            "Configuration cleared, disconnecting & removing all configured PSN instances"
        )
        for account in _configured_accounts.values():
            _LOOP.create_task(account.disconnect())
            account.events.remove_all_listeners()
        _configured_accounts.clear()
        api.configured_entities.clear()
        api.available_entities.clear()
    else:
        if device.identifier in _configured_accounts:
            _LOG.debug("Disconnecting from removed PSN %s", device.identifier)
            account = _configured_accounts.pop(device.identifier)
            _LOOP.create_task(account.disconnect())
            account.events.remove_all_listeners()

            entity_id = account.identifier
            api.configured_entities.remove(entity_id)
            api.available_entities.remove(entity_id)


async def main():
    """Start the Remote Two integration driver."""
    logging.basicConfig()

    level = os.getenv("UC_LOG_LEVEL", "DEBUG").upper()
    logging.getLogger("tv").setLevel(level)
    logging.getLogger("driver").setLevel(level)
    logging.getLogger("config").setLevel(level)
    logging.getLogger("discover").setLevel(level)
    logging.getLogger("setup_flow").setLevel(level)

    # load paired devices
    config.devices = config.Devices(
        api.config_dir_path, on_device_added, on_device_removed
    )

    # and register them as available devices.
    # Note: device will be moved to configured devices with the subscribe_events request!
    # This will also start the device connection.
    for device in config.devices.all():
        _register_available_entities(device.identifier, device.name)

    await api.init("driver.json", setup_flow.driver_setup_handler)


if __name__ == "__main__":
    _LOOP.run_until_complete(main())
    _LOOP.run_forever()
