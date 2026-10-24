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
    npsso: str = field(default="", repr=False)
    """NPSSO token for PSN authentication."""
    ps_device: dict[str, Any] = field(default_factory=dict, repr=False)
    """Remote Play profiles, or legacy playdirector credentials awaiting migration."""
    psn_account_id: str = ""
    launch_device_id: str = ""
    console_host_id: str = ""
    console_name: str = ""
