"""Single entrypoint — runs GitHub + Learn + Context7 + ADO in one process.

Locust spawns each User class according to its `fixed_count`, which each
class reads from env (GITHUB_USERS, LEARN_USERS, CONTEXT7_USERS, ADO_USERS).
Total VUs = sum of all four. Set `--users N` on the CLI / locust.conf to
match the sum, otherwise Locust will warn.

Run locally:
    uv run locust -f multi.py
Run a single class locally:
    uv run locust -f multi.py LearnUser --headless -u 5 -r 1 -t 30s
Run in Azure Load Testing:
    az load test update -f azure-loadtest.yaml ...
    az load test-run create --test-id ... ...
"""
from ado import ADOUser  # noqa: F401
from context7 import Context7User  # noqa: F401
from github import GitHubUser  # noqa: F401
from learn import LearnUser  # noqa: F401
