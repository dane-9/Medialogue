"""Coordinate torrent archive writes with local snapshot capture.

The observer commits before releasing this gate, so the SQLite snapshot and
archive copies cannot straddle an in-flight observation transaction. Remote
uploads run after the gate is released.
"""
import asyncio

archive_checkpoint = asyncio.Lock()
