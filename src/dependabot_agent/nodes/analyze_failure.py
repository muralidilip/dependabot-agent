"""Analyze failure and retry node."""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from dependabot_agent.helpers import (
    extract_build_content,
    get_llm,
    validate_no_dependencies_removed,
)
from dependabot_agent.logging_utils import (
    log_build_error_summary,
    log_node_error,
    log_node_info,
    log_node_progress,
    log_node_start,
    log_node_success,
    log_node_warning,
)
from dependabot_agent.prompts import ANALYZE_FAILURE_PROMPT
from dependabot_agent.state import AgentState
from dependabot_agent.tools.agent_tools import get_dependency_tree, write_build_file


def analyze_failure_and_retry_node(state: AgentState) -> dict:
    """LLM: Analyze build failure and attempt to fix it."""
    log_node_start("analyze_failure_and_retry", "Analyzing build failure")

    attempt = state.get("exclusion_attempt_count", 0) + 1
    max_retries = state.get("max_exclusion_retries", 3)
    log_node_info(f"Retry attempt {attempt}/{max_retries}")

    llm = get_llm()

    # Get fresh dependency tree
    log_node_progress("Fetching fresh dependency tree...")
    try:
        tree_result = get_dependency_tree.invoke({"workspace": state["workspace"], "build_system": state["build_system"], "context": "analyze_failure_and_retry"})
        dep_tree = tree_result.get("raw_tree", "")[:5000]
    except:
        log_node_warning("Could not fetch dependency tree")
        dep_tree = ""

    build_error = state.get("build_output", "")
    build_content = state["current_build_content"]

    log_node_progress("Analyzing failure and generating fix...")
    log_build_error_summary(build_error, max_lines=5)

    prompt = ANALYZE_FAILURE_PROMPT.format(
        build_error=build_error[:2000],
        build_content=build_content,
        dep_tree=dep_tree,
    )

    messages = [
        SystemMessage(content="You are a build diagnostic expert."),
        HumanMessage(content=prompt),
    ]

    response = llm.invoke(messages)
    new_content = extract_build_content(response.content)

    # Validate that no dependencies were removed
    is_valid, removed_deps = validate_no_dependencies_removed(
        state["current_build_content"],
        new_content,
        state["build_system"]
    )

    if not is_valid:
        log_node_error(f"LLM removed dependencies: {removed_deps}")
        log_node_warning("Rejecting LLM output - keeping current build file")
        # Keep the current content as-is rather than using bad LLM output
        new_content = state["current_build_content"]

    try:
        write_build_file.invoke({
            "workspace": state["workspace"],
            "content": new_content,
        })
        # Check if content actually changed
        content_changed = new_content.strip() != state["original_build_content"].strip()

        log_node_success("Applied fixes based on failure analysis")

        return {
            "current_build_content": new_content,
            "exclusion_attempt_count": attempt,
            "has_changes": content_changed,
            "messages": [AIMessage(content="Analyzed failure and applied fixes")],
        }
    except Exception as e:
        log_node_error("Failed to apply fixes", e)
        return {
            "messages": [AIMessage(content=f"Failed to apply fixes: {e}")],
            "error": str(e),
        }

