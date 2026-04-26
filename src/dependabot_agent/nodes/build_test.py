"""Build test node."""

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


def build_test_node(state: AgentState) -> dict:
    """Deterministic: Run build and tests."""
    log_node_start("build_test", "Running full build and tests")

    try:
        result = run_build_and_test.invoke({"workspace": state["workspace"], "build_system": state["build_system"], "context": "build_test"})
        success = result.get("success", False)
        output = result.get("stdout", "") + "\n" + result.get("stderr", "")

        log_build_result(success, "Build and test")
        if not success:
            log_build_error_summary(output)

        return {
            "build_success": success,
            "build_output": output[:4000],
            "upgrade_attempt_count": state.get("upgrade_attempt_count", 0) + 1,
            "messages": [AIMessage(content=f"Build {'succeeded' if success else 'failed'}")],
        }
    except Exception as e:
        log_node_error("Build error", e)
        return {
            "build_success": False,
            "build_output": str(e),
            "upgrade_attempt_count": state.get("upgrade_attempt_count", 0) + 1,
            "messages": [AIMessage(content=f"Build error: {e}")],
        }

