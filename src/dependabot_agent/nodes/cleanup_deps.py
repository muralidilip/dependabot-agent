"""Cleanup dependencies node."""

from __future__ import annotations

import json

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from dependabot_agent.helpers import (
    analyze_pins_for_cleanup,
    extract_build_content,
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
from dependabot_agent.prompts import CLEANUP_DECISION_PROMPT
from dependabot_agent.state import AgentState
from dependabot_agent.tools.agent_tools import get_dependency_tree, write_build_file


def _format_pins_for_llm(analyzed_pins: list[dict]) -> str:
    """Format analyzed pins into a readable string for the LLM."""
    if not analyzed_pins:
        return "No pinned dependencies found."

    lines = []
    for i, pin in enumerate(analyzed_pins, 1):
        lines.append(f"### Pin {i}: {pin['group_id']}:{pin['artifact_id']}:{pin['version']}")
        lines.append(f"**Classification**: {pin['classification']}")
        lines.append(f"**Reason**: {pin['reason']}")

        if pin['transitive_occurrences']:
            lines.append("**Transitive Occurrences**:")
            for occ in pin['transitive_occurrences']:
                path_str = " → ".join(occ['path'])
                declared = occ.get('declared_version', occ['version'])
                resolved = occ['version']
                if declared != resolved:
                    version_info = f"Declared: {declared} → Resolved: {resolved}"
                else:
                    version_info = f"Version: {resolved}"
                lines.append(f"  - Parent: {occ['parent']}, {version_info}, Path: {path_str}")
        else:
            lines.append("**Transitive Occurrences**: None (direct dependency only)")

        lines.append("")

    return "\n".join(lines)


def _parse_llm_decisions(response_content: str) -> list[dict]:
    """Parse LLM's JSON response for cleanup decisions."""
    content = response_content.strip()

    # Extract JSON from markdown code blocks if present
    if "```json" in content:
        content = content.split("```json")[1].split("```")[0].strip()
    elif "```" in content:
        parts = content.split("```")
        if len(parts) >= 2:
            content = parts[1].strip()

    try:
        parsed = json.loads(content)
        return parsed.get("decisions", [])
    except json.JSONDecodeError:
        return []


def cleanup_deps_node(state: AgentState) -> dict:
    """Clean up redundant pinned dependencies and duplicate exclusions.

    This node performs LLM-driven cleanup:
    1. Extract all pinned dependencies from the build file
    2. For each pin, find all transitive occurrences in the dependency tree
    3. Classify pins as: direct_only (keep), redundant (can remove), version_forcing (keep)
    4. Ask LLM to confirm/override decisions
    5. Remove only pins that LLM approves for removal
    6. Also remove duplicate exclusions (deterministic pre-step)
    """
    log_node_start("cleanup_deps", "Cleaning up redundant dependencies")

    build_content = state["current_build_content"]
    build_system = state["build_system"]

    # Step 1: Get fresh dependency tree
    log_node_progress("Fetching dependency tree to analyze pins...")
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
        # Fall back to duplicate exclusion cleanup only
        log_node_progress("Checking for duplicate exclusions...")
        cleaned_content, duplicates_removed = remove_duplicate_exclusions(build_content, build_system)
        if duplicates_removed > 0:
            try:
                write_build_file.invoke({
                    "workspace": state["workspace"],
                    "content": cleaned_content,
                })
                log_node_success(f"Removed {duplicates_removed} duplicate exclusion(s)")
                return {
                    "current_build_content": cleaned_content,
                    "has_changes": True,
                    "messages": [AIMessage(content=f"Removed {duplicates_removed} duplicate exclusion(s)")],
                }
            except Exception as write_err:
                log_node_error("Failed to write build file", write_err)
        return {"messages": [AIMessage(content="No cleanup possible without dependency tree")]}

    # Step 2: Analyze all pins - find transitive occurrences and classify
    log_node_progress("Analyzing pinned dependencies...")
    analyzed_pins = analyze_pins_for_cleanup(build_content, dep_tree, build_system)

    if not analyzed_pins:
        log_node_info("No pinned dependencies found to analyze")
        # Still check for duplicate exclusions
        cleaned_content, duplicates_removed = remove_duplicate_exclusions(build_content, build_system)
        if duplicates_removed > 0:
            try:
                write_build_file.invoke({
                    "workspace": state["workspace"],
                    "content": cleaned_content,
                })
                log_node_success(f"Removed {duplicates_removed} duplicate exclusion(s)")
                return {
                    "current_build_content": cleaned_content,
                    "has_changes": True,
                    "messages": [AIMessage(content=f"Removed {duplicates_removed} duplicate exclusion(s)")],
                }
            except Exception as e:
                log_node_error("Failed to write build file", e)
        return {"messages": [AIMessage(content="No cleanup needed")]}

    # Log the analysis
    for pin in analyzed_pins:
        coord = f"{pin['group_id']}:{pin['artifact_id']}:{pin['version']}"
        log_node_progress(f"  → {coord}: {pin['classification']} - {pin['reason']}")

    # Step 3: Check if any pins are classified as redundant
    redundant_candidates = [p for p in analyzed_pins if p['classification'] == 'redundant']

    if not redundant_candidates:
        log_node_info("No redundant pins found - all pins are necessary")
        # Still check for duplicate exclusions
        cleaned_content, duplicates_removed = remove_duplicate_exclusions(build_content, build_system)
        if duplicates_removed > 0:
            try:
                write_build_file.invoke({
                    "workspace": state["workspace"],
                    "content": cleaned_content,
                })
                log_node_success(f"Removed {duplicates_removed} duplicate exclusion(s)")
                return {
                    "current_build_content": cleaned_content,
                    "has_changes": True,
                    "messages": [AIMessage(content=f"Removed {duplicates_removed} duplicate exclusion(s)")],
                }
            except Exception as e:
                log_node_error("Failed to write build file", e)
        return {"messages": [AIMessage(content="No redundant pins found")]}

    # Step 4: Ask LLM to confirm cleanup decisions
    log_node_progress(f"Found {len(redundant_candidates)} potentially redundant pin(s), consulting LLM...")

    llm = get_llm()
    pins_data = _format_pins_for_llm(analyzed_pins)
    prompt = CLEANUP_DECISION_PROMPT.format(pins_data=pins_data)

    messages = [
        SystemMessage(content="You are a dependency cleanup expert. Be conservative - only remove truly redundant pins."),
        HumanMessage(content=prompt),
    ]

    response = llm.invoke(messages)
    decisions = _parse_llm_decisions(response.content)

    if not decisions:
        log_node_warning("Could not parse LLM cleanup decisions - skipping pin cleanup")
        # Still check for duplicate exclusions
        cleaned_content, duplicates_removed = remove_duplicate_exclusions(build_content, build_system)
        if duplicates_removed > 0:
            try:
                write_build_file.invoke({
                    "workspace": state["workspace"],
                    "content": cleaned_content,
                })
                log_node_success(f"Removed {duplicates_removed} duplicate exclusion(s)")
                return {
                    "current_build_content": cleaned_content,
                    "has_changes": True,
                    "messages": [AIMessage(content=f"Removed {duplicates_removed} duplicate exclusion(s)")],
                }
            except Exception as e:
                log_node_error("Failed to write build file", e)
        return {"messages": [AIMessage(content="LLM decision parsing failed - no cleanup performed")]}

    # Step 5: Build list of pins to remove (only those LLM approved AND classified as redundant)
    pins_to_remove = []
    for decision in decisions:
        if decision.get("action", "").upper() == "REMOVE":
            group_id = decision.get("group_id", "")
            artifact_id = decision.get("artifact_id", "")

            # Safety check: only remove if our analysis also classified it as redundant
            matching_pin = next(
                (p for p in analyzed_pins
                 if p['group_id'] == group_id and p['artifact_id'] == artifact_id),
                None
            )

            if matching_pin and matching_pin['classification'] == 'redundant':
                pins_to_remove.append(matching_pin)
                log_node_progress(f"  ✓ Will remove: {group_id}:{artifact_id} - {decision.get('reason', '')}")
            elif matching_pin:
                log_node_warning(
                    f"  ✗ LLM suggested removing {group_id}:{artifact_id} but it's classified as "
                    f"'{matching_pin['classification']}' - keeping it"
                )

    # Step 6: Remove approved pins
    if pins_to_remove:
        log_node_progress(f"Removing {len(pins_to_remove)} redundant pin(s)...")
        new_content = remove_redundant_pins(build_content, pins_to_remove, build_system)

        # Validate that we only removed the approved pins
        is_valid, removed_deps = validate_no_dependencies_removed(
            state["original_build_content"],
            new_content,
            build_system
        )

        approved_coords = {f"{p['group_id']}:{p['artifact_id']}" for p in pins_to_remove}
        unexpected_removals = [d for d in removed_deps if d not in approved_coords]

        if unexpected_removals:
            log_node_error(f"Cleanup would remove unapproved dependencies: {unexpected_removals}")
            log_node_warning("Reverting pin removal - keeping current build file")
            new_content = build_content
        else:
            build_content = new_content
            log_node_success(f"Removed {len(pins_to_remove)} redundant pin(s)")

    # Step 7: Remove duplicate exclusions (deterministic)
    log_node_progress("Checking for duplicate exclusions...")
    cleaned_content, duplicates_removed = remove_duplicate_exclusions(build_content, build_system)
    if duplicates_removed > 0:
        build_content = cleaned_content
        log_node_success(f"Removed {duplicates_removed} duplicate exclusion(s)")

    # Step 8: Write final result
    made_changes = len(pins_to_remove) > 0 or duplicates_removed > 0

    if made_changes:
        try:
            write_build_file.invoke({
                "workspace": state["workspace"],
                "content": build_content,
            })
            content_changed = build_content.strip() != state["original_build_content"].strip()

            # Build summary
            changes = []
            if pins_to_remove:
                changes.append(f"{len(pins_to_remove)} redundant pin(s)")
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
