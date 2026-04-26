"""Clone repository node."""

from __future__ import annotations

from langchain_core.messages import AIMessage

from dependabot_agent.logging_utils import (
    log_node_error,
    log_node_start,
    log_node_success,
    log_workspace_info,
)
from dependabot_agent.state import AgentState
from dependabot_agent.tools.agent_tools import clone_repository


def clone_repo_node(state: AgentState) -> dict:
    """Deterministic: Clone repo and read build file."""
    log_node_start("clone_repo", f"Cloning {state.get('repo')} (develop branch)")

    try:
        result = clone_repository.invoke({"repo": state.get('repo'), "branch": "develop"})

        log_workspace_info(
            result["workspace"],
            result["build_system"],
            result["build_file"]
        )
        log_node_success("Repository cloned successfully")

        return {
            "workspace": result["workspace"],
            "build_system": result["build_system"],
            "build_file": result["build_file"],
            "original_build_content": result["build_file_content"],
            "current_build_content": result["build_file_content"],
            "messages": [AIMessage(content=f"Cloned {state['repo']}")],
            "error": "",
        }
    except Exception as e:
        log_node_error("Failed to clone repository", e)
        return {
            "messages": [AIMessage(content=f"Failed to clone: {e}")],
            "error": str(e),
        }

