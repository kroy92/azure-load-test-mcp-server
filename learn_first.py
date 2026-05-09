"""Standalone Locust script for the Microsoft Learn MCP server.

Mirrors the five-step walkthrough in docs/blog.html section 2.

Install (one-time):

    # with uv
    uv pip install "locust>=2.31"

    # or with pip in an active venv
    python -m pip install "locust>=2.31"

Run:

    uv run locust -f learn_first.py --headless -u 5 -r 1 -t 30s

    # or, inside the repo's venv:

    python -m locust -f learn_first.py --headless -u 5 -r 1 -t 30s
"""

import json
import random
import itertools

from locust import FastHttpUser, constant, task


# ---- Step 1. Define the data and target -----------------------------------

QUERIES = [
    "azure functions cold start",
    "entra id conditional access",
    "aks cluster autoscaler",
    "cosmos db throughput rules",
]

_id_counter = itertools.count(1)


class LearnUser(FastHttpUser):
    host             = "https://learn.microsoft.com"
    mcp_path         = "/api/mcp"
    protocol_version = "2025-06-18"
    session_id: str | None = None

    # Each VU waits 1s between tool calls so the load is paced and
    # the per-second numbers are predictable.
    wait_time = constant(1)

    # ---- Step 2. Initialize the session -----------------------------------

    def on_start(self):
        rpc_id = next(_id_counter)
        payload = {
            "jsonrpc": "2.0", "id": rpc_id, "method": "initialize",
            "params": {
                "protocolVersion": self.protocol_version,
                "capabilities": {},
                "clientInfo": {"name": "learn-first", "version": "1.0.0"},
            },
        }
        headers = {
            "Content-Type": "application/json",
            "Accept":       "application/json, text/event-stream",
        }
        with self.client.post(self.mcp_path, data=json.dumps(payload),
                              headers=headers, name="learn:initialize",
                              catch_response=True) as r:
            r.raise_for_status()
            self.session_id = r.headers.get("Mcp-Session-Id")
            if not self.session_id:
                r.failure("no Mcp-Session-Id"); return
            r.success()

        self._notify_initialized()
        self._tools_list()

    # ---- Step 3. Confirm handshake + list tools ---------------------------

    def _headers(self):
        return {
            "Content-Type":         "application/json",
            "Accept":               "application/json, text/event-stream",
            "MCP-Protocol-Version": self.protocol_version,
            "Mcp-Session-Id":       self.session_id,
        }

    def _notify_initialized(self):
        payload = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        with self.client.post(self.mcp_path, data=json.dumps(payload),
                              headers=self._headers(),
                              name="learn:notifications/initialized",
                              catch_response=True) as r:
            r.success() if r.status_code == 202 else r.failure(f"got {r.status_code}")

    def _tools_list(self):
        rpc_id = next(_id_counter)
        payload = {"jsonrpc": "2.0", "id": rpc_id, "method": "tools/list"}
        with self.client.post(self.mcp_path, data=json.dumps(payload),
                              headers=self._headers(),
                              name="learn:tools/list",
                              catch_response=True) as r:
            r.raise_for_status(); r.success()

    # ---- Step 4. Call a tool under load -----------------------------------

    def _call(self, tool: str, arguments: dict):
        rpc_id = next(_id_counter)
        payload = {
            "jsonrpc": "2.0", "id": rpc_id, "method": "tools/call",
            "params": {"name": tool, "arguments": arguments},
        }
        with self.client.post(self.mcp_path, data=json.dumps(payload),
                              headers=self._headers(),
                              name=f"learn:tools/call:{tool}",
                              catch_response=True) as r:
            r.raise_for_status()
            obj = self._parse_sse(r.content, rpc_id)
            if "error" in obj:
                r.failure(f"jsonrpc error: {obj['error']}"); return
            r.success()

    @staticmethod
    def _parse_sse(body: bytes, want_id: int) -> dict:
        for ev in body.decode("utf-8", "replace").split("\n\n"):
            data = "\n".join(ln[5:].lstrip() for ln in ev.splitlines()
                             if ln.startswith("data:"))
            if not data:
                continue
            obj = json.loads(data)
            if obj.get("id") == want_id:
                return obj
        return {}

    @task(1)
    def docs_search(self):
        self._call("microsoft_docs_search", {"query": random.choice(QUERIES)})

    @task(1)
    def code_sample_search(self):
        self._call("microsoft_code_sample_search", {"query": random.choice(QUERIES)})

    # ---- Step 5. Close the session cleanly --------------------------------

    def on_stop(self):
        if not self.session_id:
            return
        with self.client.delete(self.mcp_path, headers=self._headers(),
                                name="learn:DELETE",
                                catch_response=True) as r:
            r.success() if r.status_code in (200, 202, 204, 404, 405) \
                else r.failure(f"got {r.status_code}")
        self.session_id = None
