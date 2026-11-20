from __future__ import annotations
import os
import stat
from pathlib import Path, PureWindowsPath


class PolicyError(ValueError):
    pass


class WorkspacePolicy:
    """File operations are confined to a user-owned workspace.

    A workspace is not an OS sandbox. Concurrent hostile modification of its
    directory tree requires a future handle-based native broker.
    """
    def __init__(self, root: Path):
        root.mkdir(parents=True, exist_ok=True)
        self.root = root.resolve()

    def resolve(self, relative: str, permission: str = "READ") -> Path:
        if permission not in {"READ", "CREATE", "UPDATE"}:
            raise PolicyError(f"Permission {permission} is not granted")
        if not isinstance(relative, str) or not relative or len(relative) > 240:
            raise PolicyError("Expected a non-empty relative path (max 240 characters)")
        windows = PureWindowsPath(relative)
        parts = relative.replace("\\", "/").split("/")
        if windows.drive or windows.root or relative.startswith("/") or any(
            p in {"", ".", ".."} or ":" in p or p.endswith((" ", ".")) or
            p.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
            for p in parts
        ):
            raise PolicyError("Unsafe workspace path")
        candidate = self.root.joinpath(*parts)
        current = self.root
        for part in parts:
            current /= part
            try:
                reparse = bool(getattr(current.lstat(), "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
            except FileNotFoundError:
                reparse = False
            if current.is_symlink() or reparse:
                raise PolicyError("Links and junctions are not allowed")
        resolved = candidate.resolve()
        if not resolved.is_relative_to(self.root):
            raise PolicyError("Path escapes the workspace")
        return resolved
