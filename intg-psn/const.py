"""PSN Account device configuration model."""

from dataclasses import dataclass


@dataclass
class PSNConfig:
    """PSN Account device configuration."""

    identifier: str
    """Unique identifier of the device."""
    name: str
    """Friendly name of the device."""
    npsso: str
    """Credentials for different protocols."""
