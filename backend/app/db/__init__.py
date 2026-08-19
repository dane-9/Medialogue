from .base import Base

__all__ = ["Base", "async_session_factory", "engine", "get_db"]


def __getattr__(name: str):
    if name in {"async_session_factory", "engine", "get_db"}:
        from . import session

        return getattr(session, name)
    raise AttributeError(name)
