"""End with error node."""

from __future__ import annotations

from langchain_core.messages import AIMessage

from dependabot_agent.logging_utils import log_node_error, log_node_start
from dependabot_agent.state import AgentState


def end_with_error_node(state: AgentState) -> dict:
    """Terminal node for error cases."""
    log_node_start("end_with_error", "Workflow ended with error")
    error = state.get('error', 'unknown')
    log_node_error(f"Final error: {error}")
    return {
        "messages": [AIMessage(content=f"Workflow ended with error: {error}")],
    }

