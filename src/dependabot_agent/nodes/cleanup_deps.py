"""Cleanup dependencies node."""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from dependabot_agent.helpers import (
    extract_build_content,
    get_llm,
    validate_no_dependencies_removed,
)
from dependabot_agent.logging_utils import (
    log_node_error,
    log_node_info,
    log_node_progress,
    log_node_start,
    log_node_success,
    log_node_warning,
)
from dependabot_agent.prompts import CLEANUP_PROMPT
from dependabot_agent.state import AgentState
from dependabot_agent.tools.agent_tools import write_build_file


def cleanup_deps_node(state: AgentState) -> dict:
    """LLM: Clean up redundant pinned dependencies."""
    log_node_start("cleanup_deps", "Cleaning up redundant dependencies")

    llm = get_llm()

    build_content = state["current_build_content"]

    log_node_progress("Invoking LLM to clean up build file...")

    prompt = f"""{CLEANUP_PROMPT}

## Current Build File:
```
{build_content}
```

Output the cleaned build file or "NO_CHANGES":
"""

    messages = [
        SystemMessage(content="You are a build file cleaner."),
        HumanMessage(content=prompt),
    ]

    response = llm.invoke(messages)

    content = response.content.strip()
    if "NO_CHANGES" in content.upper():
        log_node_info("No cleanup needed")
        return {
            "messages": [AIMessage(content="No cleanup needed")],
        }

    new_content = extract_build_content(content)

    # Validate that no dependencies were removed (only duplicates should be removed)
    is_valid, removed_deps = validate_no_dependencies_removed(
        state["current_build_content"],
        new_content,
        state["build_system"]
    )

    if not is_valid:
        log_node_error(f"LLM removed unique dependencies: {removed_deps}")
        log_node_warning("Rejecting cleanup - keeping current build file")
        # Don't apply cleanup that removes unique dependencies
        return {
            "messages": [AIMessage(content="Cleanup rejected - would remove unique dependencies")],
        }

    try:
        write_build_file.invoke({
            "workspace": state["workspace"],
            "content": new_content,
        })
        # Check if content actually changed
        content_changed = new_content.strip() != state["original_build_content"].strip()

        log_node_success("Cleaned up build file")

        return {
            "current_build_content": new_content,
            "has_changes": content_changed,
            "messages": [AIMessage(content="Cleaned up build file")],
        }
    except Exception as e:
        log_node_error("Failed to clean build file", e)
        return {
            "messages": [AIMessage(content=f"Failed to clean build file: {e}")],
            "error": str(e),
        }

