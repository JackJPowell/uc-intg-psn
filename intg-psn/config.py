"""
Configuration handling of the integration driver.

:copyright: (c) 2023-2024 by Unfolded Circle ApS.
:license: Mozilla Public License Version 2.0, see LICENSE for more details.
"""

import dataclasses
import json
import logging
import os
from dataclasses import dataclass
from typing import Iterator

_LOG = logging.getLogger(__name__)

_CFG_FILENAME = "config.json"


@dataclass
class PSNDevice:
    """Apple TV device configuration."""

    identifier: str
    """Unique identifier of the device."""
    name: str
    """Friendly name of the device."""
    npsso2: str
    """Credentials for different protocols."""


class _EnhancedJSONEncoder(json.JSONEncoder):
    """Python dataclass json encoder."""

    def default(self, o):
        if dataclasses.is_dataclass(o):
            return dataclasses.asdict(o)
        return super().default(o)


class Devices:
    """Integration driver configuration class. Manages all configured Apple TV devices."""

    def __init__(self, data_path: str, add_handler, remove_handler):
        """
        Create a configuration instance for the given configuration path.

        :param data_path: configuration path for the configuration file and client device certificates.
        """
        self._data_path: str = data_path
        self._cfg_file_path: str = os.path.join(data_path, _CFG_FILENAME)
        self._config: list[PSNDevice] = []
        self._add_handler = add_handler
        self._remove_handler = remove_handler
        self.load()

    @property
    def data_path(self) -> str:
        """Return the configuration path."""
        return self._data_path

    def all(self) -> Iterator[PSNDevice]:
        """Get an iterator for all device configurations."""
        return iter(self._config)

    def contains(self, psn_id: str) -> bool:
        """Check if there's a device with the given device identifier."""
        for item in self._config:
            if item.identifier == psn_id:
                return True
        return False

    def add_or_update(self, psn: PSNDevice) -> None:
        """
        Add a new configured Apple TV device and persist configuration.

        The device is updated if it already exists in the configuration.
        """
        # duplicate check
        if not self.update(psn):
            self._config.append(psn)
            self.store()
            if self._add_handler is not None:
                self._add_handler(psn)

    def get(self, psn_id: str) -> PSNDevice | None:
        """Get device configuration for given identifier."""
        for item in self._config:
            if item.identifier == psn_id:
                # return a copy
                return dataclasses.replace(item)
        return None

    def update(self, psn: PSNDevice) -> bool:
        """Update a configured Apple TV device and persist configuration."""
        for item in self._config:
            if item.identifier == psn.identifier:
                item.name = psn.name
                item.npsso2 = psn.npsso2
                return self.store()
        return False

    def remove(self, psn_id: str) -> bool:
        """Remove the given device configuration."""
        psn = self.get(psn_id)
        if psn is None:
            return False
        try:
            self._config.remove(psn)
            if self._remove_handler is not None:
                self._remove_handler(psn)
            return True
        except ValueError:
            pass
        return False

    def clear(self) -> None:
        """Remove the configuration file."""
        self._config = []

        if os.path.exists(self._cfg_file_path):
            os.remove(self._cfg_file_path)

        if self._remove_handler is not None:
            self._remove_handler(None)

    def store(self) -> bool:
        """
        Store the configuration file.

        :return: True if the configuration could be saved.
        """
        try:
            with open(self._cfg_file_path, "w+", encoding="utf-8") as f:
                json.dump(self._config, f, ensure_ascii=False, cls=_EnhancedJSONEncoder)
            return True
        except OSError as err:
            _LOG.error("Cannot write the config file: %s", err)

        return False

    def load(self) -> bool:
        """
        Load the config into the config global variable.

        :return: True if the configuration could be loaded.
        """
        try:
            with open(self._cfg_file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for item in data:
                # not using PSNDevice(**item) to be able to migrate old configuration files with missing attributes
                psn = PSNDevice(
                    item.get("identifier"), item.get("name", ""), item.get("npsso2", "")
                )
                self._config.append(psn)
            return True
        except OSError as err:
            _LOG.error("Cannot open the config file: %s", err)
        except (AttributeError, ValueError, TypeError) as err:
            _LOG.error("Empty or invalid config file: %s", err)

        return False


devices: Devices | None = None
