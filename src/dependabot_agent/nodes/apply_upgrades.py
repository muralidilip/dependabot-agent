"""Apply upgrades node."""

from __future__ import annotations

import json

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from dependabot_agent.helpers import (
    apply_upgrades_to_content,
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
from dependabot_agent.prompts import APPLY_UPGRADES_PROMPT
from dependabot_agent.state import AgentState
from dependabot_agent.tools.agent_tools import write_build_file


def apply_upgrades_node(state: AgentState) -> dict:
    """LLM: Apply planned upgrades to the build file."""
    log_node_start("apply_upgrades", "Applying upgrades to build file")

    planned = state.get("planned_upgrades", [])
    good = state.get("good_upgrades", [])

    # Determine which upgrades to apply
    if good:
        # Apply only good upgrades (after binary search)
        upgrades_to_apply = good
        log_node_info(f"Applying {len(good)} good upgrades (after binary search)")
    else:
        # Apply all planned upgrades (first attempt)
        upgrades_to_apply = planned
        log_node_info(f"Applying all {len(planned)} planned upgrades")

    if not upgrades_to_apply:
        # No upgrades to apply - signal that we need to try exclusions directly
        log_node_warning("No upgrades to apply - will try exclusions")
        return {
            "messages": [AIMessage(content="No upgrades to apply - will try exclusions")],
            "has_changes": False,
            "build_success": False,  # Trigger exclusions path
        }

    llm = get_llm()

    upgrades_json = json.dumps(upgrades_to_apply, indent=2, sort_keys=True)
    build_content = state["original_build_content"]

    log_node_progress("Invoking LLM to apply upgrades...")

    prompt = f"""{APPLY_UPGRADES_PROMPT}

## Upgrades to Apply:
{upgrades_json}

## Current Build File:
```
{build_content}
```

Output the complete updated build file:
"""

    messages = [
        SystemMessage(content="You are a build file editor. Output only the complete file content."),
        HumanMessage(content=prompt),
    ]

    response = llm.invoke(messages)
    new_content = extract_build_content(response.content)

    # Validate that no dependencies were removed
    is_valid, removed_deps = validate_no_dependencies_removed(
        state["original_build_content"],
        new_content,
        state["build_system"]
    )

    if not is_valid:
        log_node_error(f"LLM removed dependencies: {removed_deps}")
        log_node_warning("Falling back to programmatic upgrade application")
        # Fall back to programmatic application which is safer
        new_content = apply_upgrades_to_content(
            state["original_build_content"],
            upgrades_to_apply,
            state["build_system"]
        )

    try:
        write_build_file.invoke({
            "workspace": state["workspace"],
            "content": new_content,
        })
        # Check if content actually changed
        content_changed = new_content.strip() != state["original_build_content"].strip()

        if content_changed:
            log_node_success(f"Applied {len(upgrades_to_apply)} upgrades to build file")
        else:
            log_node_warning("Build file content unchanged after applying upgrades")

        return {
            "current_build_content": new_content,
            "has_changes": content_changed,
            "messages": [AIMessage(content=f"Applied {len(upgrades_to_apply)} upgrades")],
        }
    except Exception as e:
        log_node_error("Failed to write build file", e)
        return {
            "messages": [AIMessage(content=f"Failed to write build file: {e}")],
            "error": str(e),
            "has_changes": False,
        }

