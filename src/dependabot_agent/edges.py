"""Conditional edge functions for the graph."""

from __future__ import annotations

from typing import Literal

from dependabot_agent.state import AgentState


def check_alerts(state: AgentState) -> Literal["has_alerts", "no_alerts", "error"]:
    """Check if there are alerts to process."""
    if state.get("error"):
        return "error"
    if state.get("alerts"):
        return "has_alerts"
    return "no_alerts"


def check_clone(state: AgentState) -> Literal["success", "error"]:
    """Check if clone succeeded."""
    if state.get("error") or not state.get("workspace"):
        return "error"
    return "success"


def check_validation(state: AgentState) -> Literal["success", "failed", "no_upgrades"]:
    """Check if upgrade validation passed or if we need to skip to exclusions."""
    # If no upgrades were planned, go directly to exclusions
    if state.get("no_upgrades_possible") or not state.get("planned_upgrades"):
        return "no_upgrades"
    if state.get("build_success"):
        return "success"
    return "failed"


def check_binary_search_done(state: AgentState) -> Literal["done", "continue"]:
    """Check if binary search is complete."""
    pending = state.get("pending_upgrade_indices", [])
    if len(pending) == 0:
        return "done"
    return "continue"


def check_build_result(state: AgentState) -> Literal["verify", "need_exclusions", "failed"]:
    """Check build result and route to verification or exclusions."""
    if state.get("build_success"):
        # Build passed - now verify that vulnerabilities are actually fixed
        return "verify"
    return "failed"


def check_verification_result(state: AgentState) -> Literal["clean", "retry_upgrade", "need_exclusions"]:
    """Check if vulnerabilities are resolved after upgrade.

    Returns:
        - "clean": All vulnerabilities resolved, proceed to cleanup
        - "retry_upgrade": More upgrades available, try them
        - "need_exclusions": No more upgrades, need exclusions
    """
    if state.get("verified_clean"):
        return "clean"

    # Check if there are more upgrades to try
    planned = state.get("planned_upgrades", [])
    if planned and not state.get("no_upgrades_possible"):
        return "retry_upgrade"

    # No more upgrades possible, need exclusions
    return "need_exclusions"


def check_exclusion_result(state: AgentState) -> Literal["success", "retry", "give_up"]:
    """Check exclusion build result and decide next step."""
    if state.get("build_success"):
        return "success"

    attempts = state.get("exclusion_attempt_count", 0)
    max_retries = state.get("max_exclusion_retries", 3)

    if attempts < max_retries:
        return "retry"
    return "give_up"


def check_final_build(state: AgentState) -> Literal["success", "failed"]:
    """Check final build result."""
    if state.get("build_success"):
        return "success"
    return "failed"

