"""Safe file helpers shared by tools.

Inputs may be absolute paths returned by other tools, but they must still
resolve inside the workspace or generated roots. Outputs are always relative
to a registered root.
"""

from __future__ import annotations

from pathlib import Path

from app.security import PathSecurityError
from tools.base import ToolContext, ToolError


def resolve_input_path(ctx: ToolContext, raw: str) -> tuple[str, Path]:
    """Resolve an existing file inside workspace/ or generated/."""
    text = (raw or "").strip().replace("\\", "/")
    if not text:
        raise ToolError("A file path is required.")

    candidate = Path(text)
    if candidate.is_absolute():
        resolved = candidate.resolve()
        for root_name in ("workspace", "generated"):
            base = ctx.jail.root(root_name)
            if resolved == base or resolved.is_relative_to(base):
                if not resolved.is_file():
                    raise ToolError(f"File does not exist: {raw}")
                return root_name, resolved
        raise ToolError("That path is outside the Owlet workspace and generated folders.")

    for root_name in ("workspace", "generated"):
        try:
            resolved = ctx.jail.resolve(root_name, text, must_exist=True)
        except PathSecurityError:
            continue
        if resolved.is_file():
            return root_name, resolved
    raise ToolError(
        f"File not found in the workspace: {raw}. Download the attachment first, "
        "or use a path returned by an earlier tool."
    )


def write_bytes_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".part")
    temp.write_bytes(data)
    temp.replace(path)


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".part")
    temp.write_text(text, encoding="utf-8")
    temp.replace(path)


def relative_output(ctx: ToolContext, root_name: str, path: Path) -> str:
    return ctx.jail.relative(root_name, path)
