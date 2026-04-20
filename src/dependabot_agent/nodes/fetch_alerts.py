"""Fetch Dependabot alerts node."""

from __future__ import annotations

from langchain_core.messages import AIMessage

from dependabot_agent.github_dependabot import fetch_dependabot_alerts
from dependabot_agent.logging_utils import (
    log_alerts_summary,
    log_node_error,
    log_node_info,
    log_node_start,
    log_node_success,
)
from dependabot_agent.state import AgentState


def fetch_alerts_node(state: AgentState) -> dict:
    """Deterministic: Fetch Dependabot alerts directly."""
    log_node_start("fetch_alerts", f"Fetching Dependabot alerts for {state['repo']}")

    try:
        result = fetch_dependabot_alerts.invoke({"repo": state["repo"], "state": "open"})
        alerts = result.get("alerts", [])

        log_alerts_summary(alerts)

        if not alerts:
            log_node_info("No open Dependabot alerts found")
        else:
            log_node_success(f"Fetched {len(alerts)} open alerts")

        return {
            "alerts": alerts,
            "messages": [AIMessage(content=f"Fetched {len(alerts)} open Dependabot alerts")],
            "error": "",
            "good_upgrades": [],
            "bad_upgrades": [],
            "exclusion_attempt_count": 0,
            "max_exclusion_retries": 3,
            "has_changes": False,
            "no_upgrades_possible": False,
            "remaining_vulnerabilities": [],
            "exhausted_upgrades": [],
            "verified_clean": False,
            "verification_attempt_count": 0,
        }
    except Exception as e:
        log_node_error("Failed to fetch Dependabot alerts", e)
        return {
            "alerts": [],
            "messages": [AIMessage(content=f"Failed to fetch alerts: {e}")],
            "error": str(e),
        }

