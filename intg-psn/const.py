"""PSN Account device configuration model."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PSNConfig:
    """PSN Account device configuration."""

    identifier: str
    """Unique identifier of the device."""
    name: str
    """Friendly name of the device."""
    npsso: str
    """NPSSO token for PSN authentication."""
    ps_device: dict[str, Any] = field(default_factory=dict)
    """Optional playdirector credential dict (RemotePlayCredentials.to_dict())
    plus 'device_ip' and 'device_type' keys. Empty dict means no control configured."""
