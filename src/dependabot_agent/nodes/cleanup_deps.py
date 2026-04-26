"""Cleanup dependencies node."""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from dependabot_agent.helpers import (
    extract_build_content,
    find_redundant_pins,
    get_llm,
    remove_duplicate_exclusions,
    remove_redundant_pins,
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
from dependabot_agent.tools.agent_tools import get_dependency_tree, write_build_file


def cleanup_deps_node(state: AgentState) -> dict:
    """Clean up redundant pinned dependencies and duplicate exclusions.

    This node performs three types of cleanup:
    1. Smart cleanup: Remove pins that are now redundant because they match
       transitive dependency versions (e.g., after a parent upgrade)
    2. Duplicate exclusion cleanup: Remove duplicate exclusion entries within
       the same dependency block
    3. LLM cleanup: Remove exact duplicate entries (same dep appears twice)
    """
    log_node_start("cleanup_deps", "Cleaning up redundant dependencies")

    build_content = state["current_build_content"]
    build_system = state["build_system"]

    # Step 1: Get fresh dependency tree to check for redundant pins
    log_node_progress("Fetching dependency tree to identify redundant pins...")
    dep_tree = ""
    try:
        tree_result = get_dependency_tree.invoke({
            "workspace": state["workspace"],
            "build_system": state["build_system"],
            "context": "cleanup_deps"
        })
        dep_tree = tree_result.get("raw_tree", "")
    except Exception as e:
        log_node_warning(f"Could not get dependency tree for cleanup: {e}")
        # Continue with LLM-only cleanup

    # Step 2: Find redundant pins (pins that match transitive versions)
    redundant_pins = []
    if dep_tree:
        redundant_pins = find_redundant_pins(build_content, dep_tree, build_system)
        if redundant_pins:
            log_node_info(f"Found {len(redundant_pins)} redundant pinned dependencies:")
            for pin in redundant_pins:
                log_node_progress(f"  → {pin['group_id']}:{pin['artifact_id']}:{pin['version']} - {pin['reason']}")

    # Step 3: Remove redundant pins programmatically
    if redundant_pins:
        log_node_progress("Removing redundant pins...")
        new_content = remove_redundant_pins(build_content, redundant_pins, build_system)

        # Validate that we only removed the redundant pins, not other deps
        is_valid, removed_deps = validate_no_dependencies_removed(
            state["original_build_content"],
            new_content,
            build_system
        )

        # Check if the removed deps are all in our redundant list
        redundant_coords = {f"{p['group_id']}:{p['artifact_id']}" for p in redundant_pins}
        unexpected_removals = [d for d in removed_deps if d not in redundant_coords]

        if unexpected_removals:
            log_node_error(f"Cleanup would remove non-redundant dependencies: {unexpected_removals}")
            log_node_warning("Skipping smart cleanup - keeping current build file")
        else:
            build_content = new_content
            log_node_success(f"Removed {len(redundant_pins)} redundant pins")

    # Step 4: Remove duplicate exclusions within dependency blocks
    log_node_progress("Checking for duplicate exclusions...")
    cleaned_content, duplicates_removed = remove_duplicate_exclusions(build_content, build_system)
    if duplicates_removed > 0:
        build_content = cleaned_content
        log_node_success(f"Removed {duplicates_removed} duplicate exclusion(s)")

    # Step 5: LLM cleanup for duplicate entries (optional)
    llm = get_llm()
    log_node_progress("Checking for duplicate entries with LLM...")

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
        # No LLM changes needed - check if we made any smart cleanup changes
        made_changes = len(redundant_pins) > 0 or duplicates_removed > 0

        if made_changes:
            # We made changes in smart cleanup steps
            try:
                write_build_file.invoke({
                    "workspace": state["workspace"],
                    "content": build_content,
                })
                content_changed = build_content.strip() != state["original_build_content"].strip()

                # Build summary message
                changes = []
                if redundant_pins:
                    changes.append(f"{len(redundant_pins)} redundant pin(s)")
                if duplicates_removed > 0:
                    changes.append(f"{duplicates_removed} duplicate exclusion(s)")
                summary = f"Removed {', '.join(changes)}"

                log_node_success(f"Cleanup complete: {summary}")
                return {
                    "current_build_content": build_content,
                    "has_changes": content_changed,
                    "messages": [AIMessage(content=summary)],
                }
            except Exception as e:
                log_node_error("Failed to write cleaned build file", e)
                return {
                    "messages": [AIMessage(content=f"Failed to write build file: {e}")],
                    "error": str(e),
                }
        else:
            log_node_info("No cleanup needed")
            return {
                "messages": [AIMessage(content="No cleanup needed")],
            }

    # LLM suggested changes - validate them
    new_content = extract_build_content(content)

    # Validate that no dependencies were removed (only duplicates should be removed)
    is_valid, removed_deps = validate_no_dependencies_removed(
        state["original_build_content"],
        new_content,
        build_system
    )

    # Allow removal of redundant pins we already identified
    redundant_coords = {f"{p['group_id']}:{p['artifact_id']}" for p in redundant_pins}
    unexpected_removals = [d for d in removed_deps if d not in redundant_coords]

    if unexpected_removals:
        log_node_error(f"LLM removed unique dependencies: {unexpected_removals}")
        log_node_warning("Rejecting LLM cleanup - keeping smart cleanup only")
        # Fall back to just the smart cleanup changes
        made_changes = len(redundant_pins) > 0 or duplicates_removed > 0
        if made_changes:
            try:
                write_build_file.invoke({
                    "workspace": state["workspace"],
                    "content": build_content,
                })
                content_changed = build_content.strip() != state["original_build_content"].strip()
                return {
                    "current_build_content": build_content,
                    "has_changes": content_changed,
                    "messages": [AIMessage(content="Smart cleanup only - LLM cleanup rejected")],
                }
            except Exception as e:
                log_node_error("Failed to write build file", e)
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
