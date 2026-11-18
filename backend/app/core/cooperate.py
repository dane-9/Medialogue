"""Letting other requests in during a long pure-Python loop.

Every request and background loop shares one event loop. A loop with no
``await`` inside runs to the end before anything else is answered, so a list
build over thousands of titles held up every page meanwhile. Pausing every few
hundred items lets small requests through without slowing the build.
"""

import asyncio

#: Items handled between pauses.
PAUSE_EVERY = 250


async def pause_every(index: int, every: int = PAUSE_EVERY) -> None:
    """Give other requests a turn every ``every`` items."""

    if index and index % every == 0:
        await asyncio.sleep(0)
