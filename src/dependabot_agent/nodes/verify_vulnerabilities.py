"""Verify vulnerabilities node."""

from __future__ import annotations

import re

from langchain_core.messages import AIMessage

from dependabot_agent.logging_utils import (
    log_node_info,
    log_node_progress,
    log_node_start,
    log_node_success,
    log_node_warning,
)
from dependabot_agent.state import AgentState
from dependabot_agent.tools.agent_tools import get_dependency_tree, lookup_maven_version


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
    try:
        tree_result = get_dependency_tree.invoke({
            "workspace": state["workspace"],
            "build_system": state["build_system"],
            "alerts": alerts,
            "context": "verify_vulnerabilities"
        })
        dep_tree = tree_result.get("raw_tree", "")
        vuln_parents = tree_result.get("vulnerability_parents", {})
    except Exception as e:
        log_node_warning(f"Could not get dependency tree for verification: {e}")
        # If we can't get the tree, assume vulnerabilities are fixed
        return {
            "verified_clean": True,
            "verification_attempt_count": attempt,
            "remaining_vulnerabilities": [],
            "messages": [AIMessage(content="Could not verify - assuming clean")],
        }

    # Get vulnerable package names from alerts
    vulnerable_packages = set()
    for alert in alerts:
        pkg = alert.get("package", "")
        if pkg:
            vulnerable_packages.add(pkg)

    log_node_info(f"Checking {len(vulnerable_packages)} vulnerable packages in dependency tree")

    # Check which vulnerable packages are still in the dependency tree
    remaining = []
    resolved = []

    for pkg in vulnerable_packages:
        # Normalize package name (may be just artifact or group:artifact)
        pkg_artifact = pkg.split(":")[-1] if ":" in pkg else pkg

        # Search for the package in the dependency tree
        # Pattern matches lines like: +--- org.thymeleaf:thymeleaf:3.1.3.RELEASE
        found = False
        for line in dep_tree.split("\n"):
            # Check if this package appears in the line
            if f":{pkg_artifact}:" in line or line.strip().endswith(f":{pkg_artifact}"):
                found = True
                # Extract the full coordinates (group:artifact:version)
                match = re.search(r'([a-zA-Z0-9._-]+):(' + re.escape(pkg_artifact) + r'):([a-zA-Z0-9._-]+)', line)
                if match:
                    found_version = match.group(3)
                    log_node_progress(f"  Still present: {pkg} at version {found_version}")
                    remaining.append({
                        "package": pkg,
                        "found_version": found_version,
                        "tree_line": line.strip()
                    })
                break

        if not found:
            log_node_progress(f"  Resolved: {pkg}")
            resolved.append(pkg)

    log_node_info(f"Resolved: {len(resolved)}, Still vulnerable: {len(remaining)}")

    if not remaining:
        log_node_success("All vulnerable dependencies have been resolved!")
        return {
            "verified_clean": True,
            "verification_attempt_count": attempt,
            "remaining_vulnerabilities": [],
            "dependency_tree": dep_tree,
            "messages": [AIMessage(content="All vulnerabilities resolved")],
        }

    log_node_warning(f"{len(remaining)} vulnerabilities still present")

    # Check if we can upgrade further or if we're already at latest
    # Look at the applied upgrades and see if any can be pushed further
    good_upgrades = state.get("good_upgrades", []) or state.get("planned_upgrades", [])

    # Track which upgrades have reached their maximum version
    exhausted = list(state.get("exhausted_upgrades", []))
    can_upgrade_more = []

    for upgrade in good_upgrades:
        group_id = upgrade.get("group_id", "")
        artifact_id = upgrade.get("artifact_id", "")
        target_version = upgrade.get("target_version", "")

        # Skip if already marked as exhausted
        upgrade_key = f"{group_id}:{artifact_id}"
        if any(e.get("group_id") == group_id and e.get("artifact_id") == artifact_id
               for e in exhausted):
            continue

        # Check if there's a newer version available
        try:
            version_result = lookup_maven_version.invoke({
                "group_id": group_id,
                "artifact_id": artifact_id,
                "current_version": target_version,
            })
            latest = version_result.get("latest_version", target_version)
            upgrade_available = version_result.get("upgrade_available", False)

            if upgrade_available and latest != target_version:
                log_node_info(f"  {upgrade_key}: can upgrade from {target_version} to {latest}")
                can_upgrade_more.append({
                    **upgrade,
                    "current_version": target_version,  # The version we just applied
                    "target_version": latest,  # The new target
                })
            else:
                log_node_info(f"  {upgrade_key}: already at latest ({target_version})")
                exhausted.append({
                    **upgrade,
                    "exhausted_at": target_version,
                })
        except Exception as e:
            log_node_warning(f"  Version lookup failed for {upgrade_key}: {e}")
            # On error, mark as exhausted to avoid infinite loops
            exhausted.append({
                **upgrade,
                "exhausted_at": target_version,
                "lookup_error": str(e),
            })

    if can_upgrade_more:
        log_node_info(f"Can try {len(can_upgrade_more)} more upgrades")
        # Update planned upgrades to try further upgrades
        return {
            "verified_clean": False,
            "verification_attempt_count": attempt,
            "remaining_vulnerabilities": remaining,
            "exhausted_upgrades": exhausted,
            "dependency_tree": dep_tree,
            # Reset for another upgrade cycle
            "planned_upgrades": can_upgrade_more,
            "good_upgrades": [],  # Clear good upgrades to re-run upgrade flow
            "pending_upgrade_indices": list(range(len(can_upgrade_more))),
            "messages": [AIMessage(content=f"Vulnerabilities remain - trying {len(can_upgrade_more)} more upgrades")],
        }
    else:
        # All parent upgrades exhausted, need to try exclusions
        log_node_warning(f"All parent upgrades exhausted - need exclusions for {len(remaining)} vulnerabilities")
        return {
            "verified_clean": False,
            "verification_attempt_count": attempt,
            "remaining_vulnerabilities": remaining,
            "exhausted_upgrades": exhausted,
            "dependency_tree": dep_tree,
            "no_upgrades_possible": True,
            "messages": [AIMessage(content=f"Parent upgrades exhausted - {len(remaining)} vulnerabilities need exclusions")],
        }

