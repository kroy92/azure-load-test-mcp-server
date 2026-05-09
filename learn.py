"""Microsoft Learn MCP. Public, no auth, no session."""
from __future__ import annotations

import random

from locust import task

from _base import MCPUserBase, expect_contains, fixed_count_from_env, load_lines

QUERIES = load_lines(
    "learn_queries.txt",
    default=[
        "azure functions cold start",
        "entra id conditional access",
        "aks cluster autoscaler",
        "cosmos db throughput rules",
        "service bus session receiver",
    ],
)
CODE_QUERIES = load_lines(
    "learn_code_queries.txt",
    default=[
        "azure function http trigger python",
        "service bus receiver dotnet",
        "managed identity bicep",
    ],
)

_RESULTS = expect_contains('"results":')


class LearnUser(MCPUserBase):
    host = "https://learn.microsoft.com"
    mcp_path = "/api/mcp"
    protocol_version = "2025-06-18"
    server_label = "learn"
    fixed_count = fixed_count_from_env("LEARN_USERS", 5)

    @task(1)
    def docs_search(self) -> None:
        self._call("microsoft_docs_search", {"query": random.choice(QUERIES)}, expect=_RESULTS)

    @task(1)
    def code_sample_search(self) -> None:
        self._call("microsoft_code_sample_search", {"query": random.choice(CODE_QUERIES)}, expect=_RESULTS)
