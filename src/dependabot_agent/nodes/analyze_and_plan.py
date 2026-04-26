"""Analyze and plan upgrades node."""

from __future__ import annotations

import json

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from dependabot_agent.helpers import get_llm, version_already_applied
from dependabot_agent.logging_utils import (
    log_node_info,
    log_node_progress,
    log_node_start,
    log_node_success,
    log_node_warning,
    log_upgrades_summary,
)
from dependabot_agent.prompts import ANALYSIS_PROMPT
from dependabot_agent.state import AgentState
from dependabot_agent.tools.agent_tools import lookup_maven_version


def analyze_and_plan_node(state: AgentState) -> dict:
    """LLM: Analyze alerts and plan upgrades with version lookups."""
    log_node_start("analyze_and_plan", "Analyzing alerts and planning upgrades")

    llm = get_llm()

    # Sort alerts by package name for deterministic ordering
    sorted_alerts = sorted(state["alerts"], key=lambda a: (
        a.get("security_advisory", {}).get("severity", ""),
        a.get("dependency", {}).get("package", {}).get("name", "")
    ))
    alerts_summary = json.dumps(sorted_alerts, indent=2, sort_keys=True)
    build_content = state["current_build_content"]

    # Generate LLM-friendly vulnerability summary from vulnerability_parents
    vuln_parents = state.get("vulnerability_parents", {})
    vuln_summary = _format_vulnerability_summary(vuln_parents, state.get("alerts", []))

    log_node_progress("Invoking LLM for analysis...")

    prompt = f"""{ANALYSIS_PROMPT}

## Current Alerts:
{alerts_summary}

## Current Build File ({state['build_system']}):
```
{build_content}
```

## Vulnerability Analysis:
{vuln_summary if vuln_summary else "No vulnerability analysis available - dependency tree not parsed."}

Analyze and provide your upgrade plan as JSON.
"""

    messages = [
        SystemMessage(content="You are a dependency resolution expert."),
        HumanMessage(content=prompt),
    ]

    response = llm.invoke(messages)

    # Parse the response to extract planned upgrades
    planned_upgrades = []
    try:
        content = response.content
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]

        data = json.loads(content.strip())
        if "upgrades" in data:
            planned_upgrades = data["upgrades"]
    except (json.JSONDecodeError, IndexError, KeyError):
        log_node_warning("Failed to parse LLM response as JSON")

    log_node_progress(f"Looking up versions for {len(planned_upgrades)} upgrades...")

    # Lookup versions for each planned upgrade
    resolved_upgrades = []
    for upgrade in planned_upgrades:
        if upgrade.get("needs_lookup", True):
            try:
                version_result = lookup_maven_version.invoke({
                    "group_id": upgrade["group_id"],
                    "artifact_id": upgrade.get("artifact_id", ""),
                    "current_version": upgrade.get("current_version", ""),
                })
                resolved_upgrades.append({
                    "group_id": upgrade["group_id"],
                    "artifact_id": upgrade.get("artifact_id", ""),
                    "current_version": upgrade.get("current_version", ""),
                    "target_version": version_result.get("latest_version", upgrade.get("current_version", "")),
                    "priority": upgrade.get("priority", "direct"),
                })
            except Exception as e:
                log_node_warning(f"Version lookup failed for {upgrade.get('group_id')}: {e}")
                resolved_upgrades.append({
                    "group_id": upgrade["group_id"],
                    "artifact_id": upgrade.get("artifact_id", ""),
                    "current_version": upgrade.get("current_version", ""),
                    "target_version": upgrade.get("current_version", ""),
                    "lookup_error": str(e),
                    "priority": upgrade.get("priority", "direct"),
                })
        else:
            resolved_upgrades.append(upgrade)

    # Sort by priority: parent first, then direct, then transitive
    # Secondary sort by group_id:artifact_id for deterministic ordering
    priority_order = {"parent": 0, "direct": 1, "transitive": 2}
    resolved_upgrades.sort(key=lambda x: (
        priority_order.get(x.get("priority", "direct"), 1),
        x.get("group_id", ""),
        x.get("artifact_id", "")
    ))

    # Filter out upgrades that are already applied in the build file
    build_content = state["current_build_content"]
    build_system = state["build_system"]

    already_applied = []
    needed_upgrades = []
    for upgrade in resolved_upgrades:
        target = upgrade.get("target_version", "")
        current = upgrade.get("current_version", "")
        group_id = upgrade.get("group_id", "")

        # Skip if current == target (no change needed)
        if current == target:
            already_applied.append(upgrade)
            continue

        # Skip if target version is already in the build file
        if target and version_already_applied(build_content, group_id, target, build_system):
            already_applied.append(upgrade)
            continue

        needed_upgrades.append(upgrade)

    # Track if no parent upgrades are possible (need to go to exclusions)
    no_upgrades_possible = len(needed_upgrades) == 0 and len(state.get("alerts", [])) > 0

    log_upgrades_summary(needed_upgrades, "Planned upgrades")
    if already_applied:
        log_node_info(f"Skipped {len(already_applied)} upgrades (already at target version)")

    if no_upgrades_possible:
        log_node_warning("No parent upgrades possible - will need exclusions")
    else:
        log_node_success(f"Planned {len(needed_upgrades)} upgrades")

    return {
        "planned_upgrades": needed_upgrades,
        "pending_upgrade_indices": list(range(len(needed_upgrades))),
        "no_upgrades_possible": no_upgrades_possible,
        "messages": [
            AIMessage(content=f"Planned {len(needed_upgrades)} upgrades"),
        ],
    }


def _format_vulnerability_summary(
    vuln_parents: dict[str, list[str]],
    alerts: list[dict]
) -> str:
    """Format vulnerability_parents into an LLM-friendly summary.

    Returns a structured text showing:
    - Each vulnerable dependency
    - What root dependency brings it in (or if it's direct)
    - The patched version to upgrade to
    - Recommended action (exclusion + pin, or direct upgrade)
    """
    if not vuln_parents:
        return "No vulnerability parent information available."

    # Build alert lookup for patched versions and severity
    alert_info = {}
    for alert in alerts:
        pkg = alert.get("package", "")
        if pkg:
            alert_info[pkg] = {
                "severity": alert.get("severity", "unknown"),
                "patched_version": alert.get("first_patched_version", ""),
                "summary": alert.get("summary", ""),
            }

    lines = []
    lines.append("## Vulnerability Analysis")
    lines.append("")
    lines.append("Found the following vulnerable dependencies in the dependency tree:")
    lines.append("")

    # Group by action type
    direct_deps = []
    transitive_deps = []

    for vuln_coord, parents in vuln_parents.items():
        # Parse the vulnerable dependency coordinate
        parts = vuln_coord.split(":")
        if len(parts) >= 2:
            pkg = f"{parts[0]}:{parts[1]}"
            version = parts[2] if len(parts) > 2 else "unknown"
        else:
            pkg = vuln_coord
            version = "unknown"

        info = alert_info.get(pkg, {})
        severity = info.get("severity", "unknown")
        patched = info.get("patched_version", "latest")
        summary = info.get("summary", "")

        if "DIRECT" in parents:
            direct_deps.append({
                "coord": vuln_coord,
                "pkg": pkg,
                "version": version,
                "severity": severity,
                "patched": patched,
                "summary": summary,
            })
        else:
            transitive_deps.append({
                "coord": vuln_coord,
                "pkg": pkg,
                "version": version,
                "severity": severity,
                "patched": patched,
                "summary": summary,
                "parents": parents,
            })

    # Direct dependencies - just need version upgrade
    if direct_deps:
        lines.append("### Direct Dependencies (upgrade version in build file)")
        lines.append("")
        for dep in direct_deps:
            lines.append(f"- **{dep['coord']}** [{dep['severity'].upper()}]")
            if dep['summary']:
                lines.append(f"  - Issue: {dep['summary']}")
            lines.append(f"  - Action: Upgrade to version {dep['patched'] or 'latest'}")
            lines.append("")

    # Transitive dependencies - need exclusion from parent + pin safe version
    if transitive_deps:
        lines.append("### Transitive Dependencies (try upgrading parent first)")
        lines.append("")

        # Group by parent for clearer presentation
        parent_to_vulns: dict[str, list[dict]] = {}
        for dep in transitive_deps:
            for parent in dep['parents']:
                if parent not in parent_to_vulns:
                    parent_to_vulns[parent] = []
                parent_to_vulns[parent].append(dep)

        # Show parent-centric view
        for parent, deps in parent_to_vulns.items():
            parent_parts = parent.split(":")
            parent_name = f"{parent_parts[0]}:{parent_parts[1]}" if len(parent_parts) >= 2 else parent

            lines.append(f"**Parent: {parent}**")
            lines.append(f"- Action: Upgrade {parent_name} to latest version - this may fix all vulnerabilities below")
            lines.append("")
            lines.append("  Vulnerable transitive dependencies:")
            for dep in deps:
                lines.append(f"  - {dep['coord']} [{dep['severity'].upper()}] → needs {dep['patched'] or 'latest'}")
                if dep['summary']:
                    lines.append(f"    Issue: {dep['summary']}")
            lines.append("")

    # Summary counts
    lines.append("### Summary")
    lines.append(f"- Direct vulnerabilities to upgrade: {len(direct_deps)}")
    lines.append(f"- Transitive vulnerabilities needing exclusions: {len(transitive_deps)}")

    return "\n".join(lines)


