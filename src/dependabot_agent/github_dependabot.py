"""GitHub Dependabot alert tooling."""

from __future__ import annotations

import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from langchain_core.tools import tool

GITHUB_API_URL = "https://api.github.com"
GITHUB_API_VERSION = "2026-03-10"
DEFAULT_GITHUB_OWNER = "AAInternal"
VALID_STATES = {"open", "dismissed", "fixed"}


def _extract_message(payload: bytes) -> str:
    try:
        body = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return "Unable to parse GitHub error payload"

    message = body.get("message")
    if isinstance(message, str) and message:
        return message
    return "GitHub API request failed"


def _normalize_alert(raw: dict[str, Any]) -> dict[str, Any]:
    advisory = raw.get("security_advisory") or {}
    vulnerability = raw.get("security_vulnerability") or {}
    dependency = raw.get("dependency") or {}

    package_name = ""
    if isinstance(vulnerability.get("package"), dict):
        package_name = vulnerability["package"].get("name", "")

    if not package_name and isinstance(dependency.get("package"), dict):
        package_name = dependency["package"].get("name", "")

    # Extract vulnerable version range (e.g., "< 2.17.1", ">= 2.0.0, < 2.17.1")
    vulnerable_version_range = vulnerability.get("vulnerable_version_range", "")

    # Extract first patched/fixed version (the version to upgrade to)
    first_patched = vulnerability.get("first_patched_version") or {}
    first_patched_version = first_patched.get("identifier", "") if isinstance(first_patched, dict) else ""

    return {
        "number": raw.get("number"),
        "state": raw.get("state"),
        "severity": advisory.get("severity") or vulnerability.get("severity"),
        "summary": advisory.get("summary"),
        "package": package_name,
        "ecosystem": vulnerability.get("package", {}).get("ecosystem"),
        "manifest_path": dependency.get("manifest_path"),
        "vulnerable_version_range": vulnerable_version_range,
        "first_patched_version": first_patched_version,
        "created_at": raw.get("created_at"),
        "dismissed_at": raw.get("dismissed_at"),
        "fixed_at": raw.get("fixed_at"),
    }


def _parse_repository(repo: str) -> tuple[str, str]:
    clean_repo = repo.strip()
    if not clean_repo:
        raise ValueError("repo must be a non-empty repository name or owner/repo")

    if "/" not in clean_repo:
        owner = os.getenv("GITHUB_DEFAULT_OWNER", DEFAULT_GITHUB_OWNER).strip()
        if not owner:
            raise ValueError("GITHUB_DEFAULT_OWNER must be set when repo does not include an owner")
        return owner, clean_repo

    owner, repository = clean_repo.split("/", maxsplit=1)
    owner = owner.strip()
    repository = repository.strip()
    if not owner or not repository:
        raise ValueError("repo must be in the format owner/repo")
    return owner, repository


@tool
def fetch_dependabot_alerts(
    repo: str,
    state: str = "open",
    severity: str = "",
    ecosystem: str = "",
    per_page: int = 30,
    after: str = "",
    before: str = "",
) -> dict[str, Any]:
    """Fetch Dependabot alerts for a GitHub repository.

    Requires `GITHUB_TOKEN` in the environment. The token needs access to
    Dependabot alerts for the target repository.

    The `repo` argument may be either `owner/repo` or just `repo`. When only
    a repository name is provided, `GITHUB_DEFAULT_OWNER` is used if set,
    otherwise the default owner falls back to `AAInternal`.

    Pagination is cursor-based. Use `after` or `before` with a cursor value
    returned in a previous response to page through results.
    """
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        raise ValueError("GITHUB_TOKEN is required to call the GitHub Dependabot alerts API")

    owner, repository = _parse_repository(repo)

    if state not in VALID_STATES:
        raise ValueError(f"state must be one of: {', '.join(sorted(VALID_STATES))}")
    if not 1 <= per_page <= 100:
        raise ValueError("per_page must be between 1 and 100")

    params: dict[str, Any] = {
        "state": state,
        "per_page": per_page,
    }
    if severity:
        params["severity"] = severity
    if ecosystem:
        params["ecosystem"] = ecosystem
    if after:
        params["after"] = after
    if before:
        params["before"] = before

    url = (
        f"{GITHUB_API_URL}/repos/{owner}/{repository}/dependabot/alerts?"
        f"{urlencode(params)}"
    )
    request = Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
            "User-Agent": "dependabot-agent",
        },
        method="GET",
    )

    try:
        with urlopen(request, timeout=20) as response:
            payload = response.read()
    except HTTPError as exc:
        message = _extract_message(exc.read())
        raise RuntimeError(
            f"GitHub API error ({exc.code}) while fetching Dependabot alerts for "
            f"{owner}/{repository}: {message}"
        ) from exc
    except URLError as exc:
        raise RuntimeError(f"Network error while calling GitHub API: {exc.reason}") from exc

    try:
        alerts = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("GitHub API returned a non-JSON response") from exc

    if not isinstance(alerts, list):
        raise RuntimeError("Unexpected GitHub API payload: expected a list of alerts")

    normalized_alerts = [_normalize_alert(alert) for alert in alerts if isinstance(alert, dict)]

    return {
        "owner": owner,
        "repository": repository,
        "full_name": f"{owner}/{repository}",
        "count": len(normalized_alerts),
        "alerts": normalized_alerts,
    }

if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    result = fetch_dependabot_alerts.invoke({"repo": "muralidilip/dependabot-test"})
    print(json.dumps(result, indent=2))
