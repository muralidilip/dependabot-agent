"""Maven Central version lookup.

Queries Maven Central to find available versions for a given artifact and
returns the latest version that shares the same major version (i.e. only
minor/patch upgrades).

Primary data source is the ``maven-metadata.xml`` published to
``repo1.maven.org`` which is always up-to-date.  Falls back to the Solr
search API if the metadata file cannot be fetched.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

MAVEN_REPO_URL = "https://repo1.maven.org/maven2"
MAVEN_SEARCH_URL = "https://search.maven.org/solrsearch/select"

# Well-known Gradle plugin IDs → ordered list of Maven (group, artifact)
# candidates to try.  The lookup will try each in order and use the first
# one that returns versions matching the current major version.
# This handles cases where an artifact is renamed across major versions
# (e.g. Spring Boot 3.x vs 4.x may publish the Gradle plugin under
# different artifact IDs).
GRADLE_PLUGIN_CANDIDATES: dict[str, list[tuple[str, str]]] = {
    "org.springframework.boot": [
        ("org.springframework.boot", "spring-boot-starter-parent"),
        ("org.springframework.boot", "spring-boot"),
        ("org.springframework.boot", "spring-boot-gradle-plugin"),
    ],
    "io.spring.dependency-management": [
        ("io.spring.gradle", "dependency-management-plugin"),
    ],
}

# Patterns that indicate a pre-release / non-stable version.
# Keywords must be preceded by a separator (- or .) to avoid false
# positives on words like "RELEASE" which contain "ea".
# Trailing digits are allowed (e.g. beta1, RC2, CR1).
_PRE_RELEASE_RE = re.compile(
    r"[-.](?:alpha|beta|rc|cr|snapshot|dev|preview|incubating|ea)\d*|[-.]m\d",
    re.IGNORECASE,
)


# ── Version helpers ──────────────────────────────────────────────────────

def _parse_version(version: str) -> tuple[int, ...] | None:
    """Parse a version string into a comparable tuple of ints.

    Examples
    -------
    >>> _parse_version("3.4.1")
    (3, 4, 1)
    >>> _parse_version("4.1.115.Final")
    (4, 1, 115)
    >>> _parse_version("2.25.10")
    (2, 25, 10)
    """
    parts: list[int] = []
    for segment in version.split("."):
        m = re.match(r"^(\d+)", segment)
        if m:
            parts.append(int(m.group(1)))
        else:
            break
    return tuple(parts) if len(parts) >= 2 else None


def _is_pre_release(version: str) -> bool:
    return bool(_PRE_RELEASE_RE.search(version))


# ── Internal query ───────────────────────────────────────────────────────

def _query_metadata_xml(group_id: str, artifact_id: str) -> list[str] | None:
    """Fetch all versions from the repo's ``maven-metadata.xml``.

    This file is updated synchronously when artifacts are published and is
    therefore always authoritative — unlike the Solr search index which can
    lag behind by hours or even days for new major versions.
    """
    group_path = group_id.replace(".", "/")
    url = f"{MAVEN_REPO_URL}/{group_path}/{artifact_id}/maven-metadata.xml"
    request = Request(url, headers={"User-Agent": "dependabot-agent"})
    try:
        with urlopen(request, timeout=15) as resp:
            xml_bytes = resp.read()
    except (HTTPError, URLError, TimeoutError, OSError):
        return None

    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return None

    versions_el = root.find(".//versions")
    if versions_el is None:
        return None

    return [v.text for v in versions_el.findall("version") if v.text]


def _query_search_api(group_id: str, artifact_id: str) -> list[str] | None:
    """Fallback: query the Solr search index on search.maven.org."""
    query = f'g:"{group_id}" AND a:"{artifact_id}"'
    url = (
        f"{MAVEN_SEARCH_URL}"
        f"?q={quote(query, safe='')}"
        f"&core=gav&rows=200&wt=json"
    )
    request = Request(
        url,
        headers={
            "User-Agent": "dependabot-agent",
            "Accept": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (HTTPError, URLError, json.JSONDecodeError, TimeoutError, OSError):
        return None

    docs = data.get("response", {}).get("docs", [])
    return [doc["v"] for doc in docs if doc.get("v")]


def _query_maven_central(group_id: str, artifact_id: str) -> list[str] | None:
    """Return the list of version strings, trying metadata XML first."""
    versions = _query_metadata_xml(group_id, artifact_id)
    if versions:
        return versions
    return _query_search_api(group_id, artifact_id)


def _filter_candidates(
    versions: list[str],
    current_version: str,
    current_parsed: tuple[int, ...],
) -> list[tuple[tuple[int, ...], str]]:
    """Filter versions to same-major, non-pre-release candidates."""
    current_major = current_parsed[0]
    current_is_pre = _is_pre_release(current_version)

    candidates: list[tuple[tuple[int, ...], str]] = []
    for v in versions:
        parsed = _parse_version(v)
        if not parsed:
            continue
        if parsed[0] != current_major:
            continue
        if not current_is_pre and _is_pre_release(v):
            continue
        candidates.append((parsed, v))
    return candidates


# ── Public API ───────────────────────────────────────────────────────────

def lookup_absolute_latest_version(
    group_id: str,
    artifact_id: str,
) -> dict[str, Any]:
    """Query Maven Central for the absolute latest stable version.

    Unlike lookup_latest_version, this does NOT filter by major version.
    Use this when you need the latest version regardless of compatibility,
    e.g., when pinning excluded transitive dependencies.

    Parameters
    ----------
    group_id:
        Maven groupId (e.g. ``"org.thymeleaf"``).
    artifact_id:
        Maven artifactId (e.g. ``"thymeleaf"``).

    Returns
    -------
    dict with ``group_id``, ``artifact_id``, ``latest_version``, and ``found`` flag.
    """
    versions = _query_maven_central(group_id, artifact_id)
    if not versions:
        return {
            "group_id": group_id,
            "artifact_id": artifact_id,
            "latest_version": "",
            "found": False,
            "error": f"No versions found for {group_id}:{artifact_id}",
        }

    # Filter out pre-release versions and parse
    candidates: list[tuple[tuple[int, ...], str]] = []
    for v in versions:
        parsed = _parse_version(v)
        if not parsed:
            continue
        if _is_pre_release(v):
            continue
        candidates.append((parsed, v))

    if not candidates:
        return {
            "group_id": group_id,
            "artifact_id": artifact_id,
            "latest_version": "",
            "found": False,
            "error": f"No stable versions found for {group_id}:{artifact_id}",
        }

    # Sort by parsed version descending and pick the highest
    candidates.sort(key=lambda x: x[0], reverse=True)
    _, latest_version = candidates[0]

    return {
        "group_id": group_id,
        "artifact_id": artifact_id,
        "latest_version": latest_version,
        "found": True,
    }


def lookup_latest_version(
    group_id: str,
    artifact_id: str,
    current_version: str,
) -> dict[str, Any]:
    """Query Maven Central for the latest minor/patch upgrade.

    Parameters
    ----------
    group_id:
        Maven groupId  **or**  a Gradle plugin ID (e.g.
        ``"org.springframework.boot"``).  If a known Gradle plugin ID is
        passed, multiple candidate artifacts are tried until one returns
        versions that match the current major version.
    artifact_id:
        Maven artifactId.  Ignored when *group_id* matches a known Gradle
        plugin ID.
    current_version:
        The version currently used in the project.

    Returns
    -------
    dict with ``current_version``, ``latest_version``,
    ``same_major_versions`` (up to 10), and ``upgrade_available`` flag.
    """
    current_parsed = _parse_version(current_version)
    if not current_parsed:
        return {
            "error": f"Could not parse current version: {current_version}",
            "group_id": group_id,
            "artifact_id": artifact_id,
            "current_version": current_version,
            "latest_version": current_version,
            "upgrade_available": False,
        }

    # Build ordered list of (group, artifact) pairs to try.
    if group_id in GRADLE_PLUGIN_CANDIDATES:
        coordinate_list = GRADLE_PLUGIN_CANDIDATES[group_id]
    else:
        coordinate_list = [(group_id, artifact_id)]

    # Try each candidate; use the first that has versions for current major.
    best_candidates: list[tuple[tuple[int, ...], str]] = []
    resolved_group = group_id
    resolved_artifact = artifact_id
    last_error: str | None = None

    for g, a in coordinate_list:
        versions = _query_maven_central(g, a)
        if versions is None:
            last_error = f"Failed to query Maven Central for {g}:{a}"
            continue
        filtered = _filter_candidates(versions, current_version, current_parsed)
        if filtered:
            best_candidates = filtered
            resolved_group = g
            resolved_artifact = a
            break

    if not best_candidates:
        result: dict[str, Any] = {
            "group_id": resolved_group,
            "artifact_id": resolved_artifact,
            "current_version": current_version,
            "latest_version": current_version,
            "same_major_versions": [],
            "upgrade_available": False,
        }
        if last_error:
            result["error"] = last_error
        return result

    best_candidates.sort(key=lambda x: x[0], reverse=True)
    latest_parsed, latest_version = best_candidates[0]

    upgrade_available = latest_parsed > current_parsed

    return {
        "group_id": resolved_group,
        "artifact_id": resolved_artifact,
        "current_version": current_version,
        "latest_version": latest_version if upgrade_available else current_version,
        "same_major_versions": [v for _, v in best_candidates[:10]],
        "upgrade_available": upgrade_available,
    }

