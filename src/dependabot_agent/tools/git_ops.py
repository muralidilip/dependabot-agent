"""Git and GitHub operations: clone, branch, commit, push, create PR."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

GITHUB_API_URL = "https://api.github.com"
GITHUB_API_VERSION = "2026-03-10"
CLONE_BASE = os.path.join(os.path.expanduser("~"), ".dependabot-agent", "workspaces")


def _run(cmd: list[str], cwd: str | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a subprocess and return the result."""
    return subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, timeout=300, check=check
    )


def clone_repo(owner: str, repo: str, branch: str = "develop") -> str:
    """Clone a GitHub repository at the given branch and return the local path.

    Falls back to 'main' if the requested branch does not exist.
    """
    token = os.getenv("GITHUB_TOKEN", "")
    clone_url = f"https://x-access-token:{token}@github.com/{owner}/{repo}.git"
    workspace = os.path.join(CLONE_BASE, f"{owner}__{repo}")

    # Clean previous clone
    if os.path.exists(workspace):
        shutil.rmtree(workspace)
    os.makedirs(workspace, exist_ok=True)

    # Try the requested branch first
    result = _run(
        ["git", "clone", "--depth", "50", "--branch", branch, clone_url, workspace],
        check=False,
    )
    if result.returncode != 0:
        # Fallback to main
        _run(["git", "clone", "--depth", "50", "--branch", "main", clone_url, workspace])

    return workspace


def create_branch(workspace: str, branch_name: str) -> None:
    """Create and checkout a new branch.

    If the branch already exists locally (e.g. from a previous agent run),
    it is deleted first so we get a clean start from the current HEAD.
    """
    # Delete the local branch if it already exists
    existing = _run(["git", "branch", "--list", branch_name], cwd=workspace, check=False)
    if existing.stdout.strip():
        _run(["git", "branch", "-D", branch_name], cwd=workspace, check=False)
    _run(["git", "checkout", "-b", branch_name], cwd=workspace)


def commit_and_push(workspace: str, branch_name: str, message: str) -> None:
    """Stage all changes, commit, and push to origin.

    Handles common failure modes:
    - Shallow clone: automatically unshallows before pushing.
    - Remote branch already exists: uses force-push to overwrite it.
    - No changes to commit: raises RuntimeError.
    """
    _run(["git", "add", "-A"], cwd=workspace)

    # Check if there are any staged changes before committing
    status = _run(["git", "status", "--porcelain"], cwd=workspace, check=False)
    if not status.stdout.strip():
        raise RuntimeError("No changes to commit. The build file was not modified.")

    _run(["git", "commit", "-m", message], cwd=workspace)

    # Unshallow the clone if necessary – shallow repos can fail to push
    is_shallow = _run(
        ["git", "rev-parse", "--is-shallow-repository"], cwd=workspace, check=False
    )
    if is_shallow.stdout.strip() == "true":
        _run(["git", "fetch", "--unshallow", "origin"], cwd=workspace, check=False)

    # Try a normal push first
    result = _run(
        ["git", "push", "--set-upstream", "origin", branch_name],
        cwd=workspace,
        check=False,
    )
    if result.returncode == 0:
        return

    # If the remote branch already exists, force-push to overwrite it
    result2 = _run(
        ["git", "push", "--force", "--set-upstream", "origin", branch_name],
        cwd=workspace,
        check=False,
    )
    if result2.returncode != 0:
        raise RuntimeError(
            f"git push failed (exit {result2.returncode}).\n"
            f"stderr: {result2.stderr.strip()}"
        )


def create_pull_request(
    owner: str,
    repo: str,
    head: str,
    base: str = "develop",
    title: str = "",
    body: str = "",
) -> dict[str, Any]:
    """Open a pull request via the GitHub API and return the response."""
    token = os.getenv("GITHUB_TOKEN", "")
    url = f"{GITHUB_API_URL}/repos/{owner}/{repo}/pulls"
    payload = json.dumps({
        "title": title or f"fix: resolve Dependabot vulnerabilities ({head})",
        "body": body or "Automated PR created by dependabot-agent.",
        "head": head,
        "base": base,
    }).encode("utf-8")

    request = Request(
        url,
        data=payload,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
            "User-Agent": "dependabot-agent",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"GitHub API error ({exc.code}) creating PR for "
            f"{owner}/{repo}: {err_body}"
        ) from exc


def revert_file(workspace: str, file_path: str) -> None:
    """Revert a single file to HEAD."""
    _run(["git", "checkout", "HEAD", "--", file_path], cwd=workspace)

