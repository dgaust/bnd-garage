"""Local LAN protocol for B&D SmartDoorDevices hubs (Basestations).

Deliberately free of any Home Assistant imports so it can be unit-tested and
reasoned about on its own. The wire format (endpoints, crypto, command codes)
follows the independent research published in haids3/bnd-garage-client (MIT)
and THE-MAVER1CK's b-and-d-garage-api; see NOTICE.md at the repo root.
"""

from .client import HubClient
from .errors import (
    AuthenticationError,
    GarageError,
    HubCommandError,
    HubUnreachableError,
    PairingError,
)
from .models import (
    ActivityEntry,
    Credentials,
    DeviceStatus,
    DoorState,
    HubInfo,
    PresetAction,
    ToggleState,
)
from .pairing import pair_new_phone
from .transport import read_hub_id

__all__ = [
    "ActivityEntry",
    "AuthenticationError",
    "Credentials",
    "DeviceStatus",
    "DoorState",
    "GarageError",
    "HubClient",
    "HubCommandError",
    "HubInfo",
    "HubUnreachableError",
    "PairingError",
    "PresetAction",
    "ToggleState",
    "pair_new_phone",
    "read_hub_id",
]
