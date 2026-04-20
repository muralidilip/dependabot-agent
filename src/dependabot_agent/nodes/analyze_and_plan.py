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
    dep_tree = state.get("dependency_tree", "")[:5000]  # Truncate if too long

    log_node_progress("Invoking LLM for analysis...")

    prompt = f"""{ANALYSIS_PROMPT}

## Current Alerts:
{alerts_summary}

## Current Build File ({state['build_system']}):
```
{build_content}
```

## Dependency Tree (truncated):
```
{dep_tree}
```

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

