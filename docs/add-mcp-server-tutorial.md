# Adding a new hosted MCP server

Below is what I actually did to add Context7 (`https://mcp.context7.com/mcp`) to
this harness. The same five steps work for any other hosted MCP endpoint.

You'll need the server URL, whatever auth it requires (token, API key, OAuth,
or none), and `curl.exe` on `PATH`. PowerShell's `Invoke-WebRequest` mangles
JSON bodies often enough that I've stopped fighting it — `curl.exe` just works.

## 1. Probe with `initialize`

A single `initialize` call answers three questions: is the server reachable,
what does it call its session header (`Mcp-Session-Id` vs `mcp-session-id`, or
none at all), and what does it advertise about itself?

PowerShell-quoting is awful, so write the body to a file first:

```powershell
'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"probe","version":"1"}}}' |
  Out-File -Encoding ascii -NoNewline init.json

curl.exe -sS -m 60 -i -X POST `
  "https://mcp.context7.com/mcp" `
  -H "Content-Type: application/json" `
  -H "Accept: application/json, text/event-stream" `
  --data-binary "@init.json"
```

Relevant bits of the response:

```http
HTTP/1.1 200 OK
Content-Type: text/event-stream

event: message
data: {"jsonrpc":"2.0","id":1,"result":{
  "protocolVersion":"2025-06-18",
  "serverInfo":{"name":"Context7","version":"2.2.4"}
}}
```

So: anonymous, no session id (Context7 is fully stateless — every request
stands alone), SSE response (handled by the base class). If you got `401`
instead, add `-H "Authorization: Bearer ..."` and try again — that's the same
header you'll set in `auth_headers` later.

> **Heads up on Context7 specifically:** anonymous calls work, but the free
> tier exhausts its monthly quota fast. After that, every call returns HTTP
> 200 with a tiny `"Monthly quota exceeded."` text payload — not an error,
> just useless data. Grab a free key at <https://context7.com/dashboard> and
> set `CONTEXT7_API_KEY` in `.env` before doing anything else. The locustfile
> sends it as `Authorization: Bearer <key>`. This is exactly the kind of
> silent-success failure mode step 4 below is built to catch.

## 2. List the tools

Send `notifications/initialized` (spec requires it, returns 202), then
`tools/list`. For session-bearing servers you'd reuse the session id from
step 1; Context7 doesn't have one, so just send the calls.

```powershell
'{"jsonrpc":"2.0","method":"notifications/initialized"}' |
  Out-File -Encoding ascii -NoNewline notif.json

curl.exe -sS -m 30 -X POST "https://mcp.context7.com/mcp" `
  -H "Content-Type: application/json" `
  -H "Accept: application/json, text/event-stream" `
  -H "MCP-Protocol-Version: 2025-06-18" `
  --data-binary "@notif.json" -o NUL -w "notif=%{http_code}`n"

'{"jsonrpc":"2.0","id":2,"method":"tools/list"}' |
  Out-File -Encoding ascii -NoNewline tlist.json

curl.exe -sS -m 30 -X POST "https://mcp.context7.com/mcp" `
  -H "Content-Type: application/json" `
  -H "Accept: application/json, text/event-stream" `
  -H "MCP-Protocol-Version: 2025-06-18" `
  --data-binary "@tlist.json"
```

For Context7 this returned two tools:

- `resolve-library-id(query, libraryName)` → canonical Context7 library ID
- `query-docs(libraryId, query)` → docs + code samples for that library

Both tools mark all their fields as required — don't trust the parameter names
in the tool description, look at `inputSchema.properties` and
`inputSchema.required`. Build a sample `tools/call` payload by hand and curl
it before writing the locustfile — much easier to debug one curl than a
locust run.

## 3. Write the locustfile

Copy `locustfiles/learn.py`, change the four class attributes, and write the
tasks:

```python
# locustfiles/context7.py
from __future__ import annotations
import os
import random
from locust import task
from locustfiles._base import StatelessMCPUser, expect_not_empty, load_lines

LIBRARY_IDS = load_lines("data/context7_library_ids.txt",
                         default=["/vercel/next.js", "/reactjs/react.dev"])
TOPICS = load_lines("data/context7_topics.txt",
                    default=["how to handle errors", "how to add auth"])


class Context7User(StatelessMCPUser):
    host = "https://mcp.context7.com"
    mcp_path = "/mcp"
    protocol_version = "2025-06-18"
    server_label = "context7"   # stats prefix
    weight = 1                  # share of VUs alongside other Users
    auth_headers = (
        {"Authorization": f"Bearer {os.environ['CONTEXT7_API_KEY']}"}
        if os.environ.get("CONTEXT7_API_KEY")
        else {}
    )

    @task(3)                    # 3:1 mix — most agent calls already know the lib
    def query_docs(self) -> None:
        self._call("query-docs", {
            "libraryId": random.choice(LIBRARY_IDS),
            "query": random.choice(TOPICS),
        }, expect=expect_not_empty)

    @task(1)
    def resolve_library(self) -> None:
        self._call("resolve-library-id", {
            "query": random.choice(TOPICS),
            "libraryName": "Next.js",
        }, expect=expect_not_empty)
```

The attributes that matter:

| Attribute | What it does |
|---|---|
| `host` | Base URL — Locust prepends to `mcp_path` for every request |
| `mcp_path` | The MCP endpoint path |
| `server_label` | Prefix on every stats line (so multi-server stats don't collide) |
| `weight` | Relative VU share when run from `multi.py` |
| `fixed_count` | Pin the VU count for this class instead of letting `weight` decide |
| `auth_headers` | Static headers added to every request |

For a token-protected server:

```python
import os
auth_headers = {"Authorization": f"Bearer {os.environ['MY_SERVER_TOKEN']}"}
```

…and put `MY_SERVER_TOKEN=...` in `.env`.

## 4. Probe each tool with realistic args

Before running Locust, validate each tool returns *real* data with the args
you plan to send. Look at `scripts/probe_context7.py` for the pattern — it's
about 100 lines of stdlib `urllib`, no Locust dependency. The script:

1. Sends `initialize` (+ optional session-id capture).
2. Loops over `(label, tool_name, args)` tuples calling `tools/call`.
3. Marks each as PASS only if the response is JSON-RPC OK **and** the
   `result.content` payload isn't empty.

That last check is the one that bites you. Plenty of tools return HTTP 200 +
`isError: false` while telling you something useless: `{"count": 0,
"results": []}` (no matching ADO work items), `"Monthly quota exceeded."`
(Context7 anonymous tier), or `[]` (wrong owner/repo combination on GitHub).
If you load-test against tools that silently return nothing, you're measuring
the server's error path, not the real workload.

Run every probe before adding tasks to the locustfile. The four already in
the repo (`probe_ado.py`, `probe_github.py`, `probe_learn.py`,
`probe_context7.py`) are working examples to copy.

Add the new server to `multi.py`:

```python
from locustfiles.context7 import Context7User  # noqa: F401
```

No registry, no factory. Locust auto-discovers `User` subclasses and splits
`-u N` across them by `weight`.

## 5. Smoke test

Run the new file alone for 30 seconds with two VUs. You're looking for zero
failures, not throughput.

```powershell
uv run locust -f locustfiles/context7.py --headless -u 2 -r 1 -t 30s `
  --csv results/context7-smoke
```

Expected:

```text
POST  context7:initialize                          2  0(0.00%) ...
POST  context7:notifications/initialized           2  0(0.00%) ...
POST  context7:tools/list                          2  0(0.00%) ...
POST  context7:tools/call:query-docs              14  0(0.00%) ...
POST  context7:tools/call:resolve-library-id       5  0(0.00%) ...
```

If something fails:

| Symptom | Usual cause |
|---|---|
| `expected 202, got 4xx` on `notifications/initialized` | Wrong protocol version, missing auth, or `initialize` was rejected |
| `HTTP 401` everywhere | `auth_headers` missing or token expired |
| `unexpected content-type: ...` | Wrong `mcp_path`, or the server returned an HTML error page |
| Very slow first call (5–10s) | Cold start. Use `-r 1` |

Then run alongside the others:

```powershell
uv run locust -f locustfiles/multi.py --headless -u 30 -r 5 -t 5m --csv results/all
```

With `weight = 1` on every class, 30 VUs split evenly. Bias the mix with
`weight = 3` (3× as many VUs of that class) or pin exactly with
`fixed_count = 5`.
