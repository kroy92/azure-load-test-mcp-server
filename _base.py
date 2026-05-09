"""Thin MCP base class. Per-server files subclass and declare host + tasks.

Flat layout: this file lives next to mcp_client.py, github.py, learn.py,
context7.py, ado.py, multi.py — same code runs locally and in Azure Load
Testing engines.
"""
from __future__ import annotations

import json
import os
import random
import sys
import time
from pathlib import Path
from typing import Any, Callable, ClassVar

# Make sibling modules importable regardless of how this file is invoked
# (locust -f multi.py, python multi.py, ALT engine, etc.).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from locust import FastHttpUser
from locust.exception import StopUser

from mcp_client import base_headers, next_id, parse_sse_for_id


# ---- assertion helpers (importable from per-server files) ----
def tool_text(obj: dict) -> str:
    """Concatenate all text-typed content blocks from a tools/call result."""
    result = (obj or {}).get("result") or {}
    content = result.get("content") or []
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
    return "\n".join(parts)


def tool_structured(obj: dict) -> Any:
    """Return result.structuredContent if present (per MCP spec), else None."""
    return (obj or {}).get("result", {}).get("structuredContent")


def expect_contains(*needles: str, case_sensitive: bool = False) -> Callable[[dict], str | None]:
    """Build an `expect=` that fails unless every needle appears in the text content."""
    def _check(obj: dict) -> str | None:
        text = tool_text(obj)
        haystack = text if case_sensitive else text.lower()
        for needle in needles:
            n = needle if case_sensitive else needle.lower()
            if n not in haystack:
                return f"missing {needle!r} in response (got {len(text)} chars)"
        return None
    return _check


def expect_any_contains(*needles: str, case_sensitive: bool = False) -> Callable[[dict], str | None]:
    """Build an `expect=` that passes if any one of the needles appears in the text content."""
    def _check(obj: dict) -> str | None:
        text = tool_text(obj)
        haystack = text if case_sensitive else text.lower()
        for needle in needles:
            n = needle if case_sensitive else needle.lower()
            if n in haystack:
                return None
        return f"none of {needles!r} found in response (got {len(text)} chars)"
    return _check


def expect_not_empty(obj: dict) -> str | None:
    """Fail if the response has no text content and no structuredContent."""
    if tool_text(obj).strip():
        return None
    if tool_structured(obj) not in (None, {}, []):
        return None
    return "empty result (no text content, no structuredContent)"


def _load_dotenv(path: str = ".env") -> None:
    """Tiny .env loader — sets os.environ for KEY=VALUE lines if not already set."""
    p = Path(path)
    if not p.exists():
        return
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


_load_dotenv()


def load_lines(path: str | Path, *, default: list[str]) -> list[str]:
    """Load one-string-per-line workload data; fall back to a small default list.

    Resolves `path` first relative to cwd, then relative to this file's dir,
    so tests work whether you launch from project root, from a notebook, or
    from an ALT engine working directory.
    """
    candidates = [Path(path), Path(__file__).resolve().parent / Path(path).name]
    for p in candidates:
        if p.exists():
            out: list[str] = []
            with p.open("r", encoding="utf-8") as f:
                for raw in f:
                    line = raw.strip()
                    if line and not line.startswith("#"):
                        out.append(line)
            if out:
                return out
    return list(default)


def fixed_count_from_env(var: str, default: int) -> int:
    """Read a per-class VU count from env, with a sensible fallback default."""
    try:
        return max(0, int(os.environ.get(var, str(default))))
    except ValueError:
        return default


class MCPUserBase(FastHttpUser):
    """Abstract base — per-server subclasses set host, mcp_path, server_label."""

    abstract = True

    mcp_path: ClassVar[str] = "/mcp"
    protocol_version: ClassVar[str] = "2025-06-18"
    server_label: ClassVar[str] = "mcp"
    auth_headers: ClassVar[dict[str, str]] = {}
    # Per MCP 2025-06-18 §2.5, session management is OPTIONAL. Stateful servers
    # (e.g. GitHub MCP) return `Mcp-Session-Id` on initialize and require it
    # on subsequent POSTs; stateless servers (Learn, ADO Remote, Context7) do
    # not. Subclasses targeting stateless servers should set this to False or
    # inherit from `StatelessMCPUser`.
    requires_session: ClassVar[bool] = True

    network_timeout = 30.0
    connection_timeout = 10.0
    session_id: str | None = None

    # ---- agent-shaped think time ----
    # 80% short intra-turn pause, 20% longer inter-turn idle. Tunable via env.
    _intra_min: ClassVar[float] = float(os.environ.get("MCP_INTRA_MIN", "0.1"))
    _intra_max: ClassVar[float] = float(os.environ.get("MCP_INTRA_MAX", "0.5"))
    _turn_min: ClassVar[float] = float(os.environ.get("MCP_TURN_MIN", "5"))
    _turn_max: ClassVar[float] = float(os.environ.get("MCP_TURN_MAX", "30"))
    _turn_prob: ClassVar[float] = float(os.environ.get("MCP_TURN_PROB", "0.2"))

    def wait_time(self) -> float:
        if random.random() < self._turn_prob:
            return random.uniform(self._turn_min, self._turn_max)
        return random.uniform(self._intra_min, self._intra_max)

    def on_start(self) -> None:
        self._initialize()
        if self.requires_session and not self.session_id:
            raise StopUser()
        self._notify_initialized()
        self._tools_list()

    def _tools_list(self) -> None:
        self._jsonrpc("tools/list", None, name=f"{self.server_label}:tools/list")

    def _extra_headers(self) -> dict[str, str]:
        """Headers merged into every request. Override to mint/refresh tokens."""
        return self.auth_headers

    def _call(
        self,
        tool: str,
        arguments: dict,
        *,
        name: str | None = None,
        expect: "Callable[[dict], str | None] | None" = None,
    ) -> dict | None:
        return self._jsonrpc(
            "tools/call",
            {"name": tool, "arguments": arguments},
            name=name or f"{self.server_label}:tools/call:{tool}",
            expect=expect,
        )

    def _jsonrpc(
        self,
        method: str,
        params: dict | None,
        *,
        name: str,
        expect: "Callable[[dict], str | None] | None" = None,
    ) -> dict | None:
        rpc_id = next_id()
        payload: dict = {"jsonrpc": "2.0", "id": rpc_id, "method": method}
        if params is not None:
            payload["params"] = params
        headers = base_headers(
            protocol_version=self.protocol_version,
            session_id=self.session_id,
            extra=self._extra_headers(),
            include_protocol=method != "initialize",
        )
        start = time.perf_counter()
        with self.client.post(
            self.mcp_path,
            data=json.dumps(payload),
            headers=headers,
            name=name,
            stream=True,
            catch_response=True,
        ) as resp:
            try:
                resp.raise_for_status()
            except Exception as exc:  # noqa: BLE001
                resp.failure(f"HTTP {resp.status_code}: {exc}")
                return None

            if method == "initialize":
                sid = resp.headers.get("Mcp-Session-Id") or resp.headers.get("mcp-session-id")
                if self.requires_session:
                    if not sid:
                        resp.failure("initialize did not return Mcp-Session-Id")
                        return None
                    self.session_id = sid
                elif sid:
                    self.session_id = sid

            ctype = (resp.headers.get("Content-Type") or "").lower()
            body = resp.content
            if "text/event-stream" in ctype:
                obj = parse_sse_for_id(body, rpc_id)
            elif "application/json" in ctype:
                obj = json.loads(body) if body else {}
            else:
                resp.failure(f"unexpected content-type: {ctype!r}")
                return None

            resp.request_meta["response_time"] = (time.perf_counter() - start) * 1000.0
            resp.request_meta["response_length"] = len(body)
            if isinstance(obj, dict) and "error" in obj:
                resp.failure(f"jsonrpc error: {obj['error']}")
                return None
            if (
                method == "tools/call"
                and isinstance(obj, dict)
                and isinstance(obj.get("result"), dict)
                and obj["result"].get("isError")
            ):
                snippet = (tool_text(obj) or "")[:160]
                resp.failure(f"tool isError=true: {snippet!r}")
                return None
            if expect is not None and isinstance(obj, dict):
                try:
                    reason = expect(obj)
                except Exception as exc:  # noqa: BLE001
                    resp.failure(f"assertion raised: {exc}")
                    return None
                if reason:
                    resp.failure(f"assertion failed: {reason}")
                    return None
            resp.success()
            return obj

    def _initialize(self) -> None:
        self._jsonrpc(
            "initialize",
            {
                "protocolVersion": self.protocol_version,
                "capabilities": {},
                "clientInfo": {"name": "locust-mcp-load", "version": "1.0.0"},
            },
            name=f"{self.server_label}:initialize",
        )

    def _notify_initialized(self) -> None:
        payload = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        with self.client.post(
            self.mcp_path,
            data=json.dumps(payload),
            headers=base_headers(
                protocol_version=self.protocol_version,
                session_id=self.session_id,
                extra=self._extra_headers(),
            ),
            name=f"{self.server_label}:notifications/initialized",
            catch_response=True,
        ) as resp:
            if resp.status_code == 202:
                resp.success()
            else:
                resp.failure(f"expected 202, got {resp.status_code}")

    def on_stop(self) -> None:
        self._terminate_session()

    def _terminate_session(self) -> None:
        if not self.requires_session or not self.session_id:
            return
        headers = base_headers(
            protocol_version=self.protocol_version,
            session_id=self.session_id,
            extra=self._extra_headers(),
        )
        try:
            with self.client.delete(
                self.mcp_path,
                headers=headers,
                name=f"{self.server_label}:DELETE",
                catch_response=True,
            ) as resp:
                if resp.status_code in (200, 202, 204, 404, 405):
                    resp.success()
                else:
                    resp.failure(f"unexpected DELETE status {resp.status_code}")
        except Exception:  # noqa: BLE001
            pass
        finally:
            self.session_id = None


class StatelessMCPUser(MCPUserBase):
    """Base for spec-compliant Streamable HTTP servers that run session-less."""

    abstract = True
    requires_session: ClassVar[bool] = False
