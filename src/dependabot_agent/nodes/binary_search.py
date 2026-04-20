"""Binary search upgrades node."""

from __future__ import annotations

from langchain_core.messages import AIMessage

from dependabot_agent.helpers import apply_upgrades_to_content
from dependabot_agent.logging_utils import (
    log_binary_search_state,
    log_node_error,
    log_node_info,
    log_node_progress,
    log_node_start,
    log_node_success,
    log_node_warning,
)
from dependabot_agent.state import AgentState
from dependabot_agent.tools.agent_tools import (
    revert_build_file,
    run_compile_only,
    write_build_file,
)


def binary_search_upgrades_node(state: AgentState) -> dict:
    """Identify bad upgrades using binary search approach.

    Strategy: Test upgrades in halves to minimize compile attempts.
    For N upgrades, worst case is O(N) compiles if ALL are bad,
    but typically O(log N) if only a few are bad.
    """
    log_node_start("binary_search_upgrades", "Finding bad upgrades via binary search")

    planned = state.get("planned_upgrades", [])
    pending = state.get("pending_upgrade_indices", [])
    good = list(state.get("good_upgrades", []))
    bad = list(state.get("bad_upgrades", []))
    build_output = state.get("build_output", "")
    # Track indices we still need to test after current batch
    deferred_indices = list(state.get("deferred_upgrade_indices", []))

    log_binary_search_state(len(pending), len(good), len(bad), len(deferred_indices))

    if len(pending) == 0:
        # Check if we have deferred indices to process
        if deferred_indices:
            log_node_progress(f"Processing {len(deferred_indices)} deferred upgrades")
            return {
                "pending_upgrade_indices": deferred_indices,
                "deferred_upgrade_indices": [],
                "messages": [AIMessage(content=f"Processing {len(deferred_indices)} deferred upgrades")],
            }
        # No more upgrades to test
        log_node_success("Binary search complete")
        return {
            "messages": [AIMessage(content="Binary search complete")],
            "pending_upgrade_indices": [],
        }

    if len(pending) == 1:
        # Single upgrade - mark it as bad since validation failed
        idx = pending[0]
        upgrade = planned[idx]
        bad.append({
            **upgrade,
            "failure_reason": build_output[:500],
        })
        artifact = upgrade.get('artifact_id', upgrade.get('group_id'))
        log_node_warning(f"Marked as bad: {artifact}")
        # Continue with deferred if any
        return {
            "bad_upgrades": bad,
            "pending_upgrade_indices": deferred_indices if deferred_indices else [],
            "deferred_upgrade_indices": [],
            "messages": [AIMessage(content=f"Marked upgrade as bad: {artifact}")],
        }

    # Split in half and test first half
    mid = len(pending) // 2
    first_half = pending[:mid]
    second_half = pending[mid:]

    log_node_progress(f"Testing first half ({len(first_half)} upgrades)")

    # Apply only first half of upgrades
    first_half_upgrades = [planned[i] for i in first_half]

    # Revert to original and apply first half only
    try:
        revert_build_file.invoke({"workspace": state["workspace"]})

        new_content = apply_upgrades_to_content(
            state["original_build_content"],
            first_half_upgrades,
            state["build_system"]
        )

        write_build_file.invoke({
            "workspace": state["workspace"],
            "content": new_content,
        })

        # Test first half
        result = run_compile_only.invoke({"workspace": state["workspace"], "context": "binary_search_upgrades"})

        if result.get("success"):
            # First half is good, problem is in second half
            log_node_info(f"First half passed - {len(first_half)} upgrades are good")
            for i in first_half:
                if planned[i] not in good:
                    good.append(planned[i])
            return {
                "good_upgrades": good,
                "pending_upgrade_indices": second_half,
                "current_build_content": new_content,
                "messages": [AIMessage(content=f"First half ({len(first_half)} upgrades) passed, testing second half")],
            }
        else:
            # Problem is in first half, defer second half for later testing
            log_node_info(f"First half failed - narrowing down. Deferred {len(second_half)} for later")
            return {
                "pending_upgrade_indices": first_half,  # Recurse on first half
                "deferred_upgrade_indices": deferred_indices + second_half,  # Save second half for later
                "build_output": result.get("stdout", "")[:2000],
                "messages": [AIMessage(content=f"First half failed, narrowing down. Deferred {len(second_half)} for later.")],
            }
    except Exception as e:
        log_node_error("Binary search error", e)
        return {
            "messages": [AIMessage(content=f"Binary search error: {e}")],
            "error": str(e),
        }

