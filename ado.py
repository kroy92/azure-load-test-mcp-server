"""Azure DevOps Remote MCP Server. Entra ID bearer auth.

Endpoint:  https://mcp.dev.azure.com/{ADO_ORG}
Transport: Streamable HTTP (POST + SSE), no session id
Auth:      Authorization: Bearer <Entra token>

Two modes — selected automatically at import:

  Local mode: ADO_MCP_TOKEN is unset. We mint tokens on demand using the
              cached `az login` (DefaultAzureCredential). Auto-refresh.
              Run `az login --tenant <tenant-id-of-your-ADO-org>` first.

  Cloud mode: ADO_MCP_TOKEN is set (Azure Load Testing engines read it from
              Key Vault via the YAML `secrets:` block). Token is static and
              expires ~1h after minting — refresh with
              scripts/refresh-ado-token.ps1 before each cloud run.
"""
from __future__ import annotations

import json
import os
import random
from urllib.request import urlopen

from locust import task

from _base import StatelessMCPUser, expect_contains, fixed_count_from_env, load_lines

ADO_ORG = os.environ.get("ADO_ORG", "")
ADO_PROJECT = os.environ.get("ADO_PROJECT", "")
ADO_MCP_TOKEN = os.environ.get("ADO_MCP_TOKEN", "")


def _discover_ado_scope(org: str) -> str:
    """RFC 9728 OAuth Protected Resource metadata to learn the audience."""
    url = f"https://mcp.dev.azure.com/.well-known/oauth-protected-resource/{org}"
    with urlopen(url, timeout=10) as resp:  # noqa: S310 - well-known HTTPS endpoint
        return json.load(resp)["scopes_supported"][0]


# Resolve token provider once at import.
if ADO_MCP_TOKEN:
    # Cloud mode: pre-minted bearer in env (KV-injected).
    def _get_ado_token() -> str:
        return ADO_MCP_TOKEN
elif ADO_ORG:
    # Local mode: mint via DefaultAzureCredential, auto-refreshed by SDK.
    from azure.identity import DefaultAzureCredential, get_bearer_token_provider

    _scope = _discover_ado_scope(ADO_ORG)
    _provider = get_bearer_token_provider(DefaultAzureCredential(), _scope)

    def _get_ado_token() -> str:
        return _provider()
else:
    def _get_ado_token() -> str:
        return ""


BUG_IDS = [int(s) for s in load_lines("ado_bug_ids.txt", default=["1", "2", "3"])]
QUERIES = load_lines(
    "ado_queries.txt",
    default=["login error", "performance regression", "crash", "telemetry", "timeout"],
)

_WIT_GET = expect_contains('"id":', '"fields":')
_WIT_MY = expect_contains('"url":"https://dev.azure.com/')
_WIT_SEARCH = expect_contains('"count":', '"results":')
_REPO_LIST = expect_contains('"isDisabled":')
_BUILD_LIST = expect_contains('"buildNumber":')


class ADOUser(StatelessMCPUser):
    host = "https://mcp.dev.azure.com"
    mcp_path = f"/{ADO_ORG}"
    protocol_version = "2025-06-18"
    server_label = "ado"
    fixed_count = fixed_count_from_env("ADO_USERS", 5)
    # Static headers only — Authorization is minted per-request via
    # `_extra_headers()`. X-MCP-Readonly filters out write/mutation tools.
    auth_headers = {"X-MCP-Readonly": "true"}

    def _extra_headers(self) -> dict[str, str]:
        return {
            **self.auth_headers,
            "Authorization": f"Bearer {_get_ado_token()}",
        }

    def on_start(self) -> None:
        if not ADO_ORG or not ADO_PROJECT:
            raise RuntimeError(
                "ADOUser requires ADO_ORG and ADO_PROJECT env vars. "
                "Local: also run `az login --tenant <tenant-id>`. "
                "Cloud: also set ADO_MCP_TOKEN (use scripts/refresh-ado-token.ps1)."
            )
        super().on_start()

    @task(3)
    def get_bug(self) -> None:
        self._call(
            "wit_work_item",
            {"action": "get", "id": random.choice(BUG_IDS), "project": ADO_PROJECT},
            expect=_WIT_GET,
        )

    @task(2)
    def my_work_items(self) -> None:
        self._call(
            "wit_work_item",
            {"action": "my", "project": ADO_PROJECT, "type": "assignedtome", "top": 25},
            expect=_WIT_MY,
        )

    @task(2)
    def search_workitems(self) -> None:
        self._call(
            "search_workitem",
            {"searchText": random.choice(QUERIES), "project": [ADO_PROJECT], "top": 10},
            expect=_WIT_SEARCH,
        )

    @task(2)
    def list_repos(self) -> None:
        self._call(
            "repo_repository",
            {"action": "list", "project": ADO_PROJECT, "top": 50},
            expect=_REPO_LIST,
        )

    @task(1)
    def list_recent_builds(self) -> None:
        self._call(
            "pipelines_build",
            {"action": "list", "project": ADO_PROJECT, "top": 25},
            expect=_BUILD_LIST,
        )
