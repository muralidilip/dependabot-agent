"""Final build node."""

from __future__ import annotations

from langchain_core.messages import AIMessage

from dependabot_agent.logging_utils import (
    log_build_error_summary,
    log_build_result,
    log_node_error,
    log_node_start,
)
from dependabot_agent.state import AgentState
from dependabot_agent.tools.agent_tools import run_build_and_test


def final_build_node(state: AgentState) -> dict:
    """Deterministic: Final build verification."""
    log_node_start("final_build", "Running final build verification")

    try:
        result = run_build_and_test.invoke({"workspace": state["workspace"], "build_system": state["build_system"], "context": "final_build"})
        success = result.get("success", False)
        output = result.get("stdout", "") + "\n" + result.get("stderr", "")

        log_build_result(success, "Final build")
        if not success:
            log_build_error_summary(output)

        return {
            "build_success": success,
            "build_output": output[:2000],
            "messages": [AIMessage(content=f"Final build {'succeeded' if success else 'failed'}")],
        }
    except Exception as e:
        log_node_error("Final build error", e)
        return {
            "build_success": False,
            "build_output": str(e),
            "messages": [AIMessage(content=f"Final build error: {e}")],
        }

