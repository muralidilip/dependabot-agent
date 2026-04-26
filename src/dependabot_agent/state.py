"""Agent state definition for the Dependabot resolver."""

from __future__ import annotations

from typing import Annotated, Any

from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class AgentState(TypedDict):
    """State shared across all nodes in the graph."""

    messages: Annotated[list, add_messages]
    repo: str
    workspace: str
    build_system: str
    build_file: str
    original_build_content: str
    current_build_content: str
    alerts: list[dict[str, Any]]
    dependency_tree: str
    vulnerability_parents: dict[str, list[str]]  # Vulnerable dep -> [root parents]
    planned_upgrades: list[dict[str, Any]]  # [{group_id, artifact_id, current, target}]
    good_upgrades: list[dict[str, Any]]  # Upgrades that passed validation
    bad_upgrades: list[dict[str, Any]]  # Upgrades that failed (with reason)
    # Binary search state
    pending_upgrade_indices: list[int]  # Indices of upgrades being tested
    deferred_upgrade_indices: list[int]  # Indices deferred for later testing
    tested_upgrade_sets: list[dict]  # History of tested sets and results
    build_success: bool
    build_output: str
    upgrade_attempt_count: int
    exclusion_attempt_count: int
    max_exclusion_retries: int
    no_upgrades_possible: bool  # True when no parent upgrades can be applied
    has_changes: bool  # True when build file was actually modified
    error: str
    pr_url: str
    # Vulnerability verification state
    remaining_vulnerabilities: list[dict[str, Any]]  # Vulnerabilities still present after upgrades
    exhausted_upgrades: list[dict[str, Any]]  # Upgrades that reached latest version but didn't fix vulns
    verified_clean: bool  # True when dependency tree confirms vulnerabilities are fixed
    verification_attempt_count: int  # Number of times we've verified the dependency tree

