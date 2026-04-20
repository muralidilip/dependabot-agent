"""Create PR node."""

from __future__ import annotations

from langchain_core.messages import AIMessage

from dependabot_agent.logging_utils import (
    log_node_error,
    log_node_progress,
    log_node_start,
    log_node_success,
    log_node_warning,
    log_pr_created,
)
from dependabot_agent.state import AgentState
from dependabot_agent.tools.agent_tools import commit_push_and_pr, create_fix_branch


def create_pr_node(state: AgentState) -> dict:
    """Deterministic: Create branch, commit, push, and PR."""
    log_node_start("create_pr", "Creating Pull Request")

    branch_name = "fix/dependabot-vulnerabilities"

    # Check if there are actual changes to commit
    current_content = state.get("current_build_content", "").strip()
    original_content = state.get("original_build_content", "").strip()
    has_changes = state.get("has_changes", False) or (current_content != original_content)

    if not has_changes:
        log_node_warning("No changes were made to the build file - nothing to commit")
        return {
            "messages": [AIMessage(content="No changes were made to the build file. Nothing to commit.")],
            "error": "No changes to commit",
        }

    try:
        log_node_progress(f"Creating branch: {branch_name}")
        create_fix_branch.invoke({
            "workspace": state["workspace"],
            "branch_name": branch_name,
        })

        # Build PR description
        alert_count = len(state.get("alerts", []))
        good_upgrades = state.get("good_upgrades", []) or state.get("planned_upgrades", [])
        bad_upgrades = state.get("bad_upgrades", [])

        pr_body = f"## Summary\nThis PR addresses {alert_count} Dependabot security vulnerabilities.\n\n"

        if good_upgrades:
            pr_body += "## Applied Upgrades\n"
            for upgrade in good_upgrades:
                pr_body += f"- `{upgrade.get('group_id', '')}:{upgrade.get('artifact_id', '')}` "
                pr_body += f"{upgrade.get('current_version', '')} → {upgrade.get('target_version', '')}\n"

        if bad_upgrades:
            pr_body += "\n## Skipped Upgrades\n"
            pr_body += "The following upgrades could not be applied due to compatibility issues:\n"
            for upgrade in bad_upgrades:
                pr_body += f"- `{upgrade.get('group_id', '')}:{upgrade.get('artifact_id', '')}` "
                pr_body += f"- Reason: {upgrade.get('failure_reason', 'compatibility issue')[:100]}\n"

        log_node_progress("Committing, pushing, and creating PR...")
        result = commit_push_and_pr.invoke({
            "workspace": state["workspace"],
            "repo": state["repo"],
            "branch_name": branch_name,
            "base_branch": "main",
            "commit_message": "fix: resolve Dependabot security vulnerabilities",
            "pr_title": f"fix: resolve {alert_count} Dependabot vulnerabilities",
            "pr_body": pr_body,
        })

        pr_url = result.get("pr_url", "")
        log_pr_created(pr_url)
        log_node_success("Pull Request created successfully")

        return {
            "pr_url": pr_url,
            "messages": [AIMessage(content=f"Created PR: {pr_url}")],
        }
    except Exception as e:
        log_node_error("Failed to create PR", e)
        return {
            "messages": [AIMessage(content=f"Failed to create PR: {e}")],
            "error": str(e),
        }

