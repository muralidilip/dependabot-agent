"""LangGraph agent that resolves Dependabot vulnerability warnings.

Workflow (Deterministic StateGraph):
  1. fetch_alerts        – Deterministic: Fetch open Dependabot alerts from GitHub.
  2. clone_repo          – Deterministic: Clone the develop branch; read the build file.
  3. get_dep_tree        – Deterministic: Get dependency tree for analysis.
  4. analyze_and_plan    – LLM: Analyze alerts + build file + dep tree, plan upgrades.
  5. apply_upgrades      – LLM: Apply all upgrades to build file.
  6. validate_upgrades   – Deterministic: Compile to check if upgrades are valid.
  7. check_validation    – Conditional: If pass → build_test. If fail → binary_search_upgrades.
  8. binary_search       – Identify bad upgrades via binary search (min LLM calls).
  9. apply_good_upgrades – Apply only the good upgrades.
  10. build_test         – Deterministic: Build & test all good upgrades.
  11. verify_vulns       – Deterministic: Check if vulnerable deps still exist in tree.
                          If vulns remain AND more upgrades available → retry upgrades.
                          If vulns remain AND upgrades exhausted → try exclusions.
  12. try_exclusions     – LLM: Plan exclusions + pinned deps for remaining issues.
  13. build_test_excl    – Deterministic: Build & test exclusions.
  14. check_excl_result  – Conditional: If pass → cleanup. If fail → analyze_and_retry.
  15. analyze_failure    – LLM: Analyze build failure and retry with fixes.
  16. cleanup_deps       – LLM: Remove redundant pinned deps.
  17. final_build        – Deterministic: Final build & test.
  18. create_pr          – Deterministic: Branch, commit, push, PR.
"""

from __future__ import annotations

from langgraph.graph import END, StateGraph

from dependabot_agent.edges import (
    check_alerts,
    check_binary_search_done,
    check_build_result,
    check_clone,
    check_exclusion_result,
    check_final_build,
    check_validation,
    check_verification_result,
)
from dependabot_agent.nodes import (
    analyze_and_plan_node,
    analyze_failure_and_retry_node,
    apply_good_upgrades_node,
    apply_upgrades_node,
    binary_search_upgrades_node,
    build_test_exclusions_node,
    build_test_node,
    cleanup_deps_node,
    clone_repo_node,
    create_pr_node,
    end_with_error_node,
    fetch_alerts_node,
    final_build_node,
    get_dep_tree_node,
    try_exclusions_node,
    validate_upgrades_node,
    verify_vulnerabilities_node,
)
from dependabot_agent.state import AgentState


def build_graph():
    """Construct and compile the deterministic LangGraph StateGraph."""
    workflow = StateGraph(AgentState)

    # Add all nodes
    workflow.add_node("fetch_alerts", fetch_alerts_node)
    workflow.add_node("clone_repo", clone_repo_node)
    workflow.add_node("get_dep_tree", get_dep_tree_node)
    workflow.add_node("analyze_and_plan", analyze_and_plan_node)
    workflow.add_node("apply_upgrades", apply_upgrades_node)
    workflow.add_node("validate_upgrades", validate_upgrades_node)
    workflow.add_node("binary_search_upgrades", binary_search_upgrades_node)
    workflow.add_node("apply_good_upgrades", apply_good_upgrades_node)
    workflow.add_node("build_test", build_test_node)
    workflow.add_node("verify_vulnerabilities", verify_vulnerabilities_node)
    workflow.add_node("try_exclusions", try_exclusions_node)
    workflow.add_node("build_test_exclusions", build_test_exclusions_node)
    workflow.add_node("analyze_failure_and_retry", analyze_failure_and_retry_node)
    workflow.add_node("cleanup_deps", cleanup_deps_node)
    workflow.add_node("final_build", final_build_node)
    workflow.add_node("create_pr", create_pr_node)
    workflow.add_node("end_with_error", end_with_error_node)

    # Set entry point
    workflow.set_entry_point("fetch_alerts")

    # Entry flow
    workflow.add_conditional_edges(
        "fetch_alerts",
        check_alerts,
        {
            "has_alerts": "clone_repo",
            "no_alerts": END,
            "error": "end_with_error",
        }
    )

    workflow.add_conditional_edges(
        "clone_repo",
        check_clone,
        {
            "success": "get_dep_tree",
            "error": "end_with_error",
        }
    )

    # Analysis flow
    workflow.add_edge("get_dep_tree", "analyze_and_plan")
    workflow.add_edge("analyze_and_plan", "apply_upgrades")
    workflow.add_edge("apply_upgrades", "validate_upgrades")

    # Validation routing
    workflow.add_conditional_edges(
        "validate_upgrades",
        check_validation,
        {
            "success": "build_test",
            "failed": "binary_search_upgrades",
            "no_upgrades": "try_exclusions",  # Skip directly to exclusions when no upgrades possible
        }
    )

    # Binary search loop
    workflow.add_conditional_edges(
        "binary_search_upgrades",
        check_binary_search_done,
        {
            "done": "apply_good_upgrades",
            "continue": "binary_search_upgrades",  # Loop back for more searching
        }
    )

    workflow.add_edge("apply_good_upgrades", "build_test")

    # Build result routing - route to verification on success
    workflow.add_conditional_edges(
        "build_test",
        check_build_result,
        {
            "verify": "verify_vulnerabilities",  # Build passed, verify vulns are fixed
            "failed": "try_exclusions",
        }
    )

    # Verification result routing
    workflow.add_conditional_edges(
        "verify_vulnerabilities",
        check_verification_result,
        {
            "clean": "cleanup_deps",  # All vulnerabilities resolved
            "retry_upgrade": "apply_upgrades",  # Try more upgrades
            "need_exclusions": "try_exclusions",  # Upgrades exhausted, try exclusions
        }
    )

    # Exclusion flow with retry
    workflow.add_edge("try_exclusions", "build_test_exclusions")

    workflow.add_conditional_edges(
        "build_test_exclusions",
        check_exclusion_result,
        {
            "success": "cleanup_deps",
            "retry": "analyze_failure_and_retry",
            "give_up": "end_with_error",
        }
    )

    # Retry loop back to build test
    workflow.add_edge("analyze_failure_and_retry", "build_test_exclusions")

    # Final steps
    workflow.add_edge("cleanup_deps", "final_build")

    workflow.add_conditional_edges(
        "final_build",
        check_final_build,
        {
            "success": "create_pr",
            "failed": "end_with_error",
        }
    )

    workflow.add_edge("create_pr", END)
    workflow.add_edge("end_with_error", END)

    return workflow.compile()


graph = build_graph()

