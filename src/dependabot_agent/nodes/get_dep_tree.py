"""Get dependency tree node."""

from __future__ import annotations

from langchain_core.messages import AIMessage

from dependabot_agent.logging_utils import (
    log_node_start,
    log_node_success,
    log_node_warning,
)
from dependabot_agent.state import AgentState
from dependabot_agent.tools.agent_tools import get_dependency_tree


def get_dep_tree_node(state: AgentState) -> dict:
    """Deterministic: Get dependency tree for the project."""
    log_node_start("get_dep_tree", "Fetching dependency tree")

    try:
        # Pass alerts to get focused vulnerability info
        alerts = state.get("alerts", [])
        result = get_dependency_tree.invoke({
            "workspace": state["workspace"],
            "build_system": state["build_system"],
            "alerts": alerts,
            "context": "get_dep_tree"
        })

        raw_tree = result.get("raw_tree", "")
        vuln_parents = result.get("vulnerability_parents", {})

        # Log vulnerability parent info
        if vuln_parents:
            log_node_success(f"Found {len(vuln_parents)} vulnerable dependencies with their parents")
        else:
            log_node_success(f"Dependency tree fetched ({len(raw_tree)} chars)")

        return {
            "dependency_tree": raw_tree,
            "vulnerability_parents": vuln_parents,
            "messages": [AIMessage(content=f"Fetched dependency tree ({len(raw_tree)} chars, {len(vuln_parents)} vulnerable deps)")],
        }
    except Exception as e:
        # Non-fatal - we can continue without the tree
        log_node_warning(f"Could not get dependency tree: {e}")
        return {
            "dependency_tree": "",
            "vulnerability_parents": {},
            "messages": [AIMessage(content=f"Warning: Could not get dependency tree: {e}")],
        }


