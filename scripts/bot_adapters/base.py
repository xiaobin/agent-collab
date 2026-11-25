"""Adapter contract kept deliberately small for stage one."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


class AdapterError(Exception):
    def __init__(self, code: str, message: str, *, exit_code: int = 5) -> None:
        super().__init__(message)
        self.code = code
        self.exit_code = exit_code


@dataclass
class PollResult:
    events: list[dict[str, Any]]
    next_cursor: Any


class Adapter:
    def approval_binding(self, config: dict[str, Any]) -> dict[str, Any]:
        """Values that must remain identical between approval and run."""
        del config
        return {}

    def poll(
        self,
        *,
        cursor: Any,
        config: dict[str, Any],
        project_root: Path,
        workspace_root: Path | None = None,
        read_scope: list[str] | None = None,
    ) -> PollResult:
        raise NotImplementedError