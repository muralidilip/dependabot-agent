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
        result = get_dependency_tree.invoke({"workspace": state["workspace"], "context": "get_dep_tree"})
        tree = result.get("tree", "")

        log_node_success(f"Dependency tree fetched ({len(tree)} chars)")

        return {
            "dependency_tree": tree,
            "messages": [AIMessage(content=f"Fetched dependency tree ({len(tree)} chars)")],
        }
    except Exception as e:
        # Non-fatal - we can continue without the tree
        log_node_warning(f"Could not get dependency tree: {e}")
        return {
            "dependency_tree": "",
            "messages": [AIMessage(content=f"Warning: Could not get dependency tree: {e}")],
        }

