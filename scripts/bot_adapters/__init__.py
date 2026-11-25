"""Stage-one bot adapters."""

from .base import AdapterError, PollResult
from .fake import FakeAdapter

ADAPTERS = {"fake": FakeAdapter}


def get_adapter(name: str):
    try:
        return ADAPTERS[name]()
    except KeyError as exc:
        raise AdapterError(
            "unsupported_adapter",
            f"adapter {name!r} is not implemented",
            exit_code=2,
        ) from exc


__all__ = ["AdapterError", "PollResult", "FakeAdapter", "get_adapter"]