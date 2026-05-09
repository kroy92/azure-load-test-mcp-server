"""MCP Streamable HTTP wire helpers. Reusable for any hosted MCP server."""
from __future__ import annotations

import itertools
import json
from typing import Any

_id_counter = itertools.count(1)


def next_id() -> int:
    return next(_id_counter)


def base_headers(
    *,
    protocol_version: str,
    session_id: str | None = None,
    extra: dict[str, str] | None = None,
    include_protocol: bool = True,
) -> dict[str, str]:
    h = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if include_protocol:
        h["MCP-Protocol-Version"] = protocol_version
    if session_id:
        h["Mcp-Session-Id"] = session_id
    if extra:
        h.update(extra)
    return h


def parse_sse_for_id(body: bytes, want_id: int) -> dict[str, Any]:
    text = body.decode("utf-8", errors="replace")
    for raw_event in text.split("\n\n"):
        data_lines = [ln[5:].lstrip() for ln in raw_event.splitlines() if ln.startswith("data:")]
        if not data_lines:
            continue
        try:
            obj = json.loads("\n".join(data_lines))
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("id") == want_id:
            return obj
    raise RuntimeError(f"SSE stream ended with no frame for id={want_id}")
