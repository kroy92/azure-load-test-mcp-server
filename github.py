"""GitHub-hosted MCP. Bearer auth via GITHUB_MCP_TOKEN.

Get a fine-grained PAT at https://github.com/settings/personal-access-tokens.
Read-only load testing only needs:
  - Repository access: Public repositories
  - Repository permissions: Metadata=Read (Issues=Read for list_issues)
  - Account permissions: Profile=Read (for get_me)
"""
from __future__ import annotations

import os
import random

from locust import task

from _base import MCPUserBase, fixed_count_from_env, load_lines

QUERIES = load_lines(
    "github_queries.txt",
    default=[
        "language:python stars:>1000",
        "language:go org:microsoft",
        "topic:mcp stars:>100",
        "language:typescript locust",
    ],
)
CODE_QUERIES = load_lines(
    "github_code_queries.txt",
    default=[
        "locust language:python",
        "FastHttpUser language:python",
        "asyncio.gather language:python",
    ],
)
FILES = [
    ("locustio", "locust", "README.md"),
    ("locustio", "locust", "pyproject.toml"),
    ("modelcontextprotocol", "servers", "README.md"),
    ("github", "github-mcp-server", "README.md"),
]
REPOS = [
    ("locustio", "locust"),
    ("modelcontextprotocol", "servers"),
    ("github", "github-mcp-server"),
]


class GitHubUser(MCPUserBase):
    host = "https://api.githubcopilot.com"
    mcp_path = "/mcp/"
    protocol_version = "2025-06-18"
    server_label = "github"
    fixed_count = fixed_count_from_env("GITHUB_USERS", 2)
    auth_headers = {
        "Authorization": f"Bearer {os.environ.get('GITHUB_MCP_TOKEN', '')}",
    }

    @task(3)
    def search_code(self) -> None:
        self._call("search_code", {"query": random.choice(CODE_QUERIES)})

    @task(2)
    def search_repos(self) -> None:
        self._call("search_repositories", {"query": random.choice(QUERIES)})

    @task(2)
    def get_file(self) -> None:
        owner, repo, path = random.choice(FILES)
        self._call("get_file_contents", {"owner": owner, "repo": repo, "path": path})

    @task(1)
    def whoami(self) -> None:
        self._call("get_me", {})

    @task(1)
    def list_issues(self) -> None:
        owner, repo = random.choice(REPOS)
        self._call("list_issues", {"owner": owner, "repo": repo, "state": "OPEN"})
