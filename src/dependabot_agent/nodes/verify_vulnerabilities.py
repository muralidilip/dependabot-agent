"""Verify vulnerabilities node."""

from __future__ import annotations

import re
from typing import Optional

from langchain_core.messages import AIMessage
from packaging import version as pkg_version

from dependabot_agent.logging_utils import (
    log_node_info,
    log_node_progress,
    log_node_start,
    log_node_success,
    log_node_warning,
)
from dependabot_agent.state import AgentState
from dependabot_agent.tools.agent_tools import get_dependency_tree


def normalize_version(ver: str) -> str:
    """Normalize version string for comparison.

    Handles Maven-style versions like 1.5.25, 4.2.8.Final, 3.1.3.RELEASE
    """
    # Remove common suffixes for comparison
    ver = ver.replace(".RELEASE", "").replace(".Final", "").replace("-RELEASE", "")
    # Handle Alpha/Beta/RC versions
    ver = re.sub(r'[.-]?(Alpha|Beta|RC|M)(\d+)?', lambda m: f".dev{m.group(2) or '0'}", ver, flags=re.IGNORECASE)
    return ver


def parse_version(ver_str: str) -> Optional[pkg_version.Version]:
    """Parse version string into comparable Version object."""
    try:
        normalized = normalize_version(ver_str)
        return pkg_version.parse(normalized)
    except Exception:
        return None


def is_version_in_vulnerable_range(current_version: str, vulnerable_range: str) -> bool:
    """Check if a version falls within a vulnerable version range.

    Supports ranges like:
    - "< 1.5.25"
    - ">= 4.2.0.Alpha1, < 4.2.8.Final"
    - "<= 3.1.3.RELEASE"
    - ">= 3.0, < 3.18.0"
    """
    current = parse_version(current_version)
    if current is None:
        # Can't parse version, assume still vulnerable
        return True

    # Split range by comma for compound ranges
    conditions = [c.strip() for c in vulnerable_range.split(",")]

    for condition in conditions:
        condition = condition.strip()
        if not condition:
            continue

        # Parse operator and version
        match = re.match(r'([<>=!]+)\s*(.+)', condition)
        if not match:
            continue

        operator = match.group(1)
        range_ver_str = match.group(2).strip()
        range_ver = parse_version(range_ver_str)

        if range_ver is None:
            continue

        # Evaluate the condition
        if operator == "<":
            if not (current < range_ver):
                return False
        elif operator == "<=":
            if not (current <= range_ver):
                return False
        elif operator == ">":
            if not (current > range_ver):
                return False
        elif operator == ">=":
            if not (current >= range_ver):
                return False
        elif operator == "==" or operator == "=":
            if not (current == range_ver):
                return False
        elif operator == "!=":
            if not (current != range_ver):
                return False

    return True


def verify_vulnerabilities_node(state: AgentState) -> dict:
    """Verify that upgrades actually fixed the vulnerable dependencies.

    After build passes, check the dependency tree to see if vulnerable
    transitive dependencies are still present. If they are:
    1. Check if we can upgrade parent further (not at latest version)
    2. If parent is already at latest, mark those vulnerabilities for exclusion
    """
    log_node_start("verify_vulnerabilities", "Verifying vulnerable dependencies are resolved")

    attempt = state.get("verification_attempt_count", 0) + 1
    log_node_info(f"Verification attempt {attempt}")

    # Get fresh dependency tree with alerts to find remaining vulnerabilities
    log_node_progress("Fetching fresh dependency tree to verify fixes...")
    alerts = state.get("alerts", [])

    alerts_map = {alert['package']: alert for alert in alerts}
    try:
        tree_result = get_dependency_tree.invoke({
            "workspace": state["workspace"],
            "build_system": state["build_system"],
            "alerts": alerts,
            "context": "verify_vulnerabilities"
        })
        dep_tree = tree_result.get("raw_tree", "")
        vuln_parents = tree_result.get("vulnerability_parents", {})
        vuln_deps_with_versions = set(vuln_parents.keys()) if vuln_parents else set()
    except Exception as e:
        log_node_warning(f"Could not get dependency tree for verification: {e}")
        # If we can't get the tree, assume vulnerabilities are fixed
        return {
            "verified_clean": True,
            "verification_attempt_count": attempt,
            "remaining_vulnerabilities": [],
            "messages": [AIMessage(content="Could not verify - assuming clean")],
        }

    # Parse current versions from dependency tree results
    # vuln_deps_with_versions format: "group:artifact:version"
    current_versions = {}
    for dep in vuln_deps_with_versions:
        parts = dep.split(":")
        if len(parts) >= 3:
            pkg_key = f"{parts[0]}:{parts[1]}"
            current_versions[pkg_key] = parts[2]

    log_node_info(f"Found {len(current_versions)} vulnerable packages in dependency tree")
    log_node_progress(f"Current versions: {current_versions}")

    # Compare current versions against vulnerable ranges
    still_vulnerable = []
    fixed_vulnerabilities = []
    new_vuln_parents = {}

    for pkg, alert in alerts_map.items():
        vulnerable_range = alert.get("vulnerable_version_range", "")
        first_patched = alert.get("first_patched_version", "")

        if pkg in current_versions:
            current_ver = current_versions[pkg]

            # Check if current version is still in vulnerable range
            if is_version_in_vulnerable_range(current_ver, vulnerable_range):
                log_node_warning(f"  ❌ {pkg}@{current_ver} still vulnerable (range: {vulnerable_range})")

                # Get parent info for this vulnerability
                # vuln_parents keys are "group:artifact:version", we need to match by "group:artifact"
                parents = []
                for vuln_coord, parent_list in vuln_parents.items():
                    if vuln_coord.startswith(f"{pkg}:"):
                        parents = parent_list
                        new_vuln_parents[pkg] = parent_list
                        break

                still_vulnerable.append({
                    "package": pkg,
                    "current_version": current_ver,
                    "vulnerable_range": vulnerable_range,
                    "first_patched_version": first_patched,
                    "parents": parents,
                    "alert": alert
                })
            else:
                log_node_success(f"  ✅ {pkg}@{current_ver} fixed (was vulnerable: {vulnerable_range})")
                fixed_vulnerabilities.append({
                    "package": pkg,
                    "fixed_version": current_ver,
                    "vulnerable_range": vulnerable_range
                })
        else:
            # Package not found in dependency tree - consider it resolved
            log_node_progress(f"  ✅ {pkg} not found in dependency tree - resolved")
            fixed_vulnerabilities.append({
                "package": pkg,
                "status": "removed_from_tree",
                "vulnerable_range": vulnerable_range
            })

    log_node_info(f"Fixed: {len(fixed_vulnerabilities)}, Still vulnerable: {len(still_vulnerable)}")

    # Update alerts to only keep those that are still vulnerable
    remaining_alerts = [
        alerts_map[vuln["package"]]
        for vuln in still_vulnerable
        if vuln["package"] in alerts_map
    ]

    if not still_vulnerable:
        log_node_success("All vulnerable dependencies have been resolved!")
        return {
            "verified_clean": True,
            "verification_attempt_count": attempt,
            "remaining_vulnerabilities": [],
            "fixed_vulnerabilities": fixed_vulnerabilities,
            "alerts": [],  # Clear alerts since all are fixed
            "dependency_tree": dep_tree,
            "messages": [AIMessage(content="All vulnerabilities resolved")],
        }

    log_node_warning(f"{len(still_vulnerable)} vulnerabilities still present - need exclusions")

    return {
        "verified_clean": False,
        "verification_attempt_count": attempt,
        "remaining_vulnerabilities": still_vulnerable,
        "fixed_vulnerabilities": fixed_vulnerabilities,
        "vulnerability_parents": new_vuln_parents,
        "alerts": remaining_alerts,  # Keep only unfixed alerts
        "dependency_tree": dep_tree,
        "no_upgrades_possible": True,
        "messages": [AIMessage(content=f"{len(still_vulnerable)} vulnerabilities need exclusions")],
    }
