"""Apply good upgrades node."""

from __future__ import annotations

from langchain_core.messages import AIMessage

from dependabot_agent.helpers import apply_upgrades_to_content
from dependabot_agent.logging_utils import (
    log_node_error,
    log_node_info,
    log_node_start,
    log_node_success,
    log_node_warning,
    log_upgrades_summary,
)
from dependabot_agent.state import AgentState
from dependabot_agent.tools.agent_tools import revert_build_file, write_build_file


def apply_good_upgrades_node(state: AgentState) -> dict:
    """Apply only the upgrades that passed validation."""
    log_node_start("apply_good_upgrades", "Applying validated upgrades")

    good = list(state.get("good_upgrades", []))
    bad = list(state.get("bad_upgrades", []))

    log_node_info(f"Good upgrades: {len(good)}, Bad upgrades: {len(bad)}")

    if not good and not bad:
        # No binary search was done, all upgrades are good
        log_node_success("All upgrades validated, proceeding")
        return {
            "messages": [AIMessage(content="All upgrades validated, proceeding")],
        }

    # If ALL upgrades were bad, we need to go to exclusions
    if not good and bad:
        log_node_warning(f"All {len(bad)} upgrades failed - proceeding to exclusions")
        log_upgrades_summary(bad, "Failed upgrades")
        return {
            "messages": [AIMessage(content=f"All {len(bad)} upgrades failed. Proceeding to exclusions.")],
            "planned_upgrades": [],
            "has_changes": False,
            "build_success": False,  # Will trigger exclusions path in build_test
        }

    # Revert and apply only good upgrades
    try:
        revert_build_file.invoke({"workspace": state["workspace"]})

        if good:
            new_content = apply_upgrades_to_content(
                state["original_build_content"],
                good,
                state["build_system"]
            )
        else:
            new_content = state["original_build_content"]

        # Add comments for bad upgrades
        if bad:
            comment_block = "\n// === DEPENDABOT: The following upgrades could not be applied ===\n"
            for b in bad:
                reason = b.get("failure_reason", "compilation error")[:100].replace("\n", " ")
                comment_block += f"// SKIP: {b.get('group_id')}:{b.get('artifact_id')} {b.get('current_version')} -> {b.get('target_version')}\n"
                comment_block += f"//       Reason: {reason}\n"
            comment_block += "// ================================================================\n"

            # Add at the top of the file after any existing header comments
            lines = new_content.split("\n")
            insert_idx = 0
            in_block_comment = False
            for i, line in enumerate(lines):
                stripped = line.strip()
                if stripped.startswith("/*"):
                    in_block_comment = True
                if in_block_comment:
                    if "*/" in stripped:
                        in_block_comment = False
                    continue
                if stripped and not stripped.startswith("//"):
                    insert_idx = i
                    break
            lines.insert(insert_idx, comment_block)
            new_content = "\n".join(lines)

        write_build_file.invoke({
            "workspace": state["workspace"],
            "content": new_content,
        })

        # Check if content actually changed
        content_changed = new_content.strip() != state["original_build_content"].strip()

        log_upgrades_summary(good, "Applied good upgrades")
        if bad:
            log_node_warning(f"Skipped {len(bad)} bad upgrades (comments added to build file)")
        log_node_success(f"Applied {len(good)} good upgrades")

        return {
            "current_build_content": new_content,
            "planned_upgrades": good if good else [],  # Update planned to only good ones for PR description
            "has_changes": content_changed,
            "messages": [AIMessage(content=f"Applied {len(good)} good upgrades (skipped {len(bad)} bad ones)")],
        }
    except Exception as e:
        log_node_error("Failed to apply good upgrades", e)
        return {
            "messages": [AIMessage(content=f"Failed to apply good upgrades: {e}")],
            "error": str(e),
            "has_changes": False,
        }

