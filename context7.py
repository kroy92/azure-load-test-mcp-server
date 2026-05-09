"""Context7 MCP (Upstash). Public, no auth, no session.

Tools:
    resolve-library-id(query, libraryName?)  -> Context7 library ID
    query-docs(libraryId, query)             -> docs + code samples
"""
from __future__ import annotations

import os
import random

from locust import task

from _base import StatelessMCPUser, expect_not_empty, fixed_count_from_env, load_lines

LIBRARIES = load_lines(
    "context7_libraries.txt",
    default=["next.js", "react", "fastapi", "langchain", "tailwindcss"],
)
LIBRARY_IDS = load_lines(
    "context7_library_ids.txt",
    default=["/vercel/next.js", "/facebook/react", "/tiangolo/fastapi"],
)
TOPICS = load_lines(
    "context7_topics.txt",
    default=[
        "how to set up server components",
        "how to add authentication",
        "how to handle errors",
    ],
)


class Context7User(StatelessMCPUser):
    host = "https://mcp.context7.com"
    mcp_path = "/mcp"
    protocol_version = "2025-06-18"
    server_label = "context7"
    fixed_count = fixed_count_from_env("CONTEXT7_USERS", 5)
    auth_headers = (
        {"Authorization": f"Bearer {os.environ['CONTEXT7_API_KEY']}"}
        if os.environ.get("CONTEXT7_API_KEY")
        else {}
    )

    @task(3)
    def query_docs(self) -> None:
        self._call(
            "query-docs",
            {
                "libraryId": random.choice(LIBRARY_IDS),
                "query": random.choice(TOPICS),
            },
            expect=expect_not_empty,
        )

    @task(1)
    def resolve_library(self) -> None:
        self._call(
            "resolve-library-id",
            {
                "query": random.choice(TOPICS),
                "libraryName": random.choice(LIBRARIES),
            },
            expect=expect_not_empty,
        )
