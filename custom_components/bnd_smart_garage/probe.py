"""Experimental push probe (off by default; enable in the integration options).

The B&D app learns of wall-button / remote presses almost instantly, but the
known protocol is request/response only. This probe tests whether the hub's
message queue (`app/res/messages`) can act as a push channel - e.g. by
holding the request open until something happens (long-poll), or by queueing
status events between calls.

It runs on its *own* HubClient session so a long-held request can't block
the coordinator's polling, and logs at DEBUG under
`custom_components.bnd_smart_garage.probe`:

- every call that returns messages, or takes longer than SLOW_SECONDS, with
  its duration and payload (door status - no credentials are logged);
- a heartbeat every HEARTBEAT_SECONDS: calls made, slowest call, how many
  returned anything, session renewals.

Press the wall button or fob while it runs and compare the timestamps with
the cover's state changes.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import TYPE_CHECKING, Any

from .protocol import GarageError, HubClient

if TYPE_CHECKING:
    from .coordinator import BndCoordinator

_LOGGER = logging.getLogger(__name__)

SLOW_SECONDS = 2.0
HEARTBEAT_SECONDS = 60.0
PAUSE_SECONDS = 1.0
ERROR_BACKOFF_SECONDS = 10.0

VARIANTS: tuple[tuple[str, str, dict[str, Any] | None], ...] = (
    ("messages(empty)", "app/res/messages", None),
    ("messages({})", "app/res/messages", {}),
)


async def run_push_probe(coordinator: BndCoordinator) -> None:
    """Loop forever (until cancelled on unload), logging what the queue does."""
    main = coordinator.client
    client = HubClient(main.host, main.credentials, main._http, main._ssl)  # noqa: SLF001
    stats = {name: {"calls": 0, "hits": 0, "slowest": 0.0} for name, _, _ in VARIANTS}
    renewals = 0
    last_token = ""
    next_heartbeat = time.monotonic() + HEARTBEAT_SECONDS
    _LOGGER.debug("push-probe started against %s", main.host)

    while True:
        for name, endpoint, body in VARIANTS:
            started = time.monotonic()
            try:
                messages = await client._call(endpoint, body)  # noqa: SLF001
            except GarageError as err:
                _LOGGER.debug("push-probe %s failed after %.1fs: %s", name, time.monotonic() - started, err)
                await asyncio.sleep(ERROR_BACKOFF_SECONDS)
                continue
            elapsed = time.monotonic() - started
            stat = stats[name]
            stat["calls"] += 1
            stat["slowest"] = max(stat["slowest"], elapsed)
            if client._token != last_token:  # noqa: SLF001
                renewals += 1 if last_token else 0
                last_token = client._token  # noqa: SLF001
            if messages or elapsed > SLOW_SECONDS:
                stat["hits"] += 1 if messages else 0
                _LOGGER.debug(
                    "push-probe %s returned %d message(s) after %.1fs: %s",
                    name,
                    len(messages),
                    elapsed,
                    json.dumps(messages)[:2000],
                )
            await asyncio.sleep(PAUSE_SECONDS)

        if time.monotonic() >= next_heartbeat:
            next_heartbeat = time.monotonic() + HEARTBEAT_SECONDS
            _LOGGER.debug("push-probe heartbeat: %s, session renewals=%d", stats, renewals)
