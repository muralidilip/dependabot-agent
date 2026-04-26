"""Validate upgrades node."""

from __future__ import annotations

from langchain_core.messages import AIMessage

from dependabot_agent.logging_utils import (
    log_build_error_summary,
    log_build_result,
    log_node_error,
    log_node_start,
)
from dependabot_agent.state import AgentState
from dependabot_agent.tools.agent_tools import run_compile_only


def validate_upgrades_node(state: AgentState) -> dict:
    """Deterministic: Quick compile to validate all upgrades."""
    log_node_start("validate_upgrades", "Compiling to validate upgrades")

    try:
        result = run_compile_only.invoke({"workspace": state["workspace"], "build_system": state["build_system"], "context": "validate_upgrades"})
        success = result.get("success", False)
        output = result.get("stdout", "") + "\n" + result.get("stderr", "")

        log_build_result(success, "Upgrade validation")
        if not success:
            log_build_error_summary(output)

        return {
            "build_success": success,
            "build_output": output[:4000],
            "messages": [AIMessage(content=f"Upgrade validation {'succeeded' if success else 'failed'}")],
        }
    except Exception as e:
        log_node_error("Validation error", e)
        return {
            "build_success": False,
            "build_output": str(e),
            "messages": [AIMessage(content=f"Validation error: {e}")],
        }

