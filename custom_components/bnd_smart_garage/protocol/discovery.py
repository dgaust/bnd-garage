"""LAN discovery: find B&D hubs without knowing their IP.

Taken from the vendor app (`LanDiscovery` in its SDK): it broadcasts the UDP
datagram {"base":1} to port 8453 and collects JSON replies for one second.
Live-verified against an SDO-7 (fw 1.5.1086), which answered in ~0.3-0.5s:

    {"bsid":"<hub id>","name":"Home New","ip":"10.0.0.252","version":"1.5.1086",
     "mode":2,"protocol":"BROADCAST","base":2,"clock":<ms>,"mono":<ms>,
     "echo":"{\\"base\\":1}"}

No credentials are involved. The hub also answers a probe sent straight to its
IP, so the same call doubles as a cheap "are you there" check.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import socket
import time

DISCOVERY_PORT = 8453
DISCOVERY_PAYLOAD = b'{"base":1}'
BROADCAST_ADDRESS = "255.255.255.255"


@dataclass(frozen=True, kw_only=True)
class DiscoveredHub:
    """One hub's discovery reply."""

    hub_id: str
    name: str
    ip: str
    version: str = ""
    mode: int = 0


def parse_reply(data: bytes) -> DiscoveredHub | None:
    """Parse a discovery reply; None for anything that isn't one."""
    try:
        body = json.loads(data.decode("utf-8", errors="replace"))
    except ValueError:
        return None
    if not isinstance(body, dict) or not body.get("bsid") or not body.get("ip"):
        return None
    return DiscoveredHub(
        hub_id=str(body["bsid"]),
        name=str(body.get("name", "")),
        ip=str(body["ip"]),
        version=str(body.get("version") or ""),
        mode=int(body.get("mode") or 0),
    )


class _Collector(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.hubs: dict[str, DiscoveredHub] = {}

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        if (hub := parse_reply(data)) is not None:
            self.hubs[hub.hub_id] = hub


async def discover_hubs(
    target: str = BROADCAST_ADDRESS, timeout: float = 1.5
) -> list[DiscoveredHub]:
    """Probe `target` (the LAN broadcast by default) and return every hub that answers."""
    loop = asyncio.get_running_loop()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.setblocking(False)
    sock.bind(("", 0))
    transport, collector = await loop.create_datagram_endpoint(_Collector, sock=sock)
    try:
        deadline = time.monotonic() + timeout
        # A second probe halfway covers a dropped first datagram (UDP over Wi-Fi).
        for _ in range(2):
            transport.sendto(DISCOVERY_PAYLOAD, (target, DISCOVERY_PORT))
            await asyncio.sleep(min(timeout / 2, max(0.0, deadline - time.monotonic())))
    finally:
        transport.close()
    return list(collector.hubs.values())


async def find_hub(hub_id: str, timeout: float = 1.5) -> DiscoveredHub | None:
    """Locate one specific hub by its ID (e.g. after its IP changed)."""
    for hub in await discover_hubs(timeout=timeout):
        if hub.hub_id == hub_id:
            return hub
    return None
