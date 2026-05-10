# azure-load-test-mcp-server

Locust load tests for hosted MCP servers — **one flat folder, runs locally and on Azure Load Testing with no code changes**.

Targets:

| Server | Auth | Session |
|---|---|---|
| GitHub MCP (`api.githubcopilot.com`) | Bearer PAT (`GITHUB_MCP_TOKEN`) | Stateful |
| Microsoft Learn MCP (`learn.microsoft.com/api/mcp`) | None | Stateful |
| Context7 (`mcp.context7.com`) | Optional bearer (`CONTEXT7_API_KEY`) | Stateless |
| Azure DevOps Remote MCP (`mcp.dev.azure.com/{org}`) | Entra ID OAuth | Stateless |

## Layout

Everything is at the repo root. No `src/`, no packages, no duplicate "cloud" copy.

```
multi.py                    entrypoint
github.py learn.py context7.py ado.py    one user class per server
_base.py mcp_client.py      shared MCP wire + Locust helpers
*.txt                       workload data (queries, ids, topics)
locust.conf                 total VUs / spawn rate / run time
azure-loadtest.yaml         ALT manifest (env vars, KV secrets, file list)
requirements.txt            for ALT engines
pyproject.toml              for local uv
scripts/refresh-ado-token.ps1   pre-mint corp token into KV before cloud runs
```

## Local

```powershell
# 1. Install
uv sync

# 2. Configure
copy .env.example .env
# edit .env — set GITHUB_MCP_TOKEN, ADO_ORG, ADO_PROJECT
az login --tenant <tenant-id-of-your-ADO-org>   # only if ADO_MCP_TOKEN unset

# 3. Run
uv run locust -f multi.py
# or a single target:
uv run locust -f multi.py LearnUser --headless -u 5 -r 1 -t 30s
```

## Cloud (Azure Load Testing)

```powershell
# 1. Refresh ADO token (corp tenant) into Key Vault
az login --tenant 72f988bf-86f1-41af-91ab-2d7cd011db47
./scripts/refresh-ado-token.ps1

# 2. Push test definition (one-time, then `update` for changes)
az login --tenant <non-prod-tenant>
az load test create -f azure-loadtest.yaml `
    --test-id mcp-multi-cloud-smoke `
    --load-test-resource <resource> --resource-group <rg>
# subsequent updates:
az load test update -f azure-loadtest.yaml `
    --test-id mcp-multi-cloud-smoke `
    --load-test-resource <resource> --resource-group <rg>

# 3. Run
$runId = "smoke-$(Get-Date -Format yyyyMMdd-HHmmss)"
az load test-run create --test-id mcp-multi-cloud-smoke `
    --test-run-id $runId `
    --load-test-resource <resource> --resource-group <rg> --no-wait
```

## How "one folder, two modes" works

| Concern | Local | Cloud (ALT) |
|---|---|---|
| Imports | Locust adds the `multi.py` dir to `sys.path` → `from _base import …` resolves. | ALT flattens uploads into the engine cwd → same `from _base import …` resolves. |
| ADO auth | `ADO_MCP_TOKEN` unset → `ado.py` mints via `DefaultAzureCredential` (auto-refresh). | YAML `secrets:` injects `ADO_MCP_TOKEN` from KV → `ado.py` uses it as a static bearer. |
| GitHub PAT | From `.env`. | YAML `secrets:` from KV. |
| VU split | Each class reads `<LABEL>_USERS` env (defaults: 2/5/5/5). | YAML `env:` sets the same vars; sum must equal `LOCUST_USERS`. |

## Tuning the load

* **Total VUs**: `users` in `locust.conf` (local) and `LOCUST_USERS` in `azure-loadtest.yaml` (cloud) — keep them equal.
* **Per-target split**: `GITHUB_USERS`, `LEARN_USERS`, `CONTEXT7_USERS`, `ADO_USERS`. Sum must equal total.
* **Think time**: `MCP_INTRA_MIN/MAX`, `MCP_TURN_MIN/MAX`, `MCP_TURN_PROB` model bursty agent-shaped traffic.

## Notes

* The ADO token is a **user** token minted from your local `az login`, valid ~1 hour. Cloud runs that need ADO must complete inside that window or be preceded by a fresh `refresh-ado-token.ps1`.
* `X-MCP-Readonly: true` is set on every ADO request so the server filters out write tools.
* Stats labels are prefixed by `server_label` (`github:`, `learn:`, `context7:`, `ado:`) so they never collide.
