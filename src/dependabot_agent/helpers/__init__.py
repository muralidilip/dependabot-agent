"""Helpers package for the Dependabot resolver agent."""

from dependabot_agent.helpers.build_file import (
    analyze_pins_for_cleanup,
    apply_upgrades_to_content,
    extract_dependencies_from_gradle,
    extract_dependencies_from_maven,
    extract_pinned_dependencies_with_version,
    find_all_transitive_occurrences,
    find_redundant_pins,
    remove_duplicate_exclusions,
    remove_redundant_pins,
    validate_no_dependencies_removed,
    version_already_applied,
)
from dependabot_agent.helpers.dep_tree import (
    build_vulnerability_parent_map,
    find_root_parent_for_transitive,
    parse_dependency_tree,
)
from dependabot_agent.helpers.exclusions import (
    apply_exclusions_to_content,
    parse_exclusions_json,
)
from dependabot_agent.helpers.llm import extract_build_content, get_llm

__all__ = [
    # Build file helpers
    "analyze_pins_for_cleanup",
    "apply_upgrades_to_content",
    "extract_dependencies_from_gradle",
    "extract_dependencies_from_maven",
    "extract_pinned_dependencies_with_version",
    "find_all_transitive_occurrences",
    "find_redundant_pins",
    "remove_duplicate_exclusions",
    "remove_redundant_pins",
    "validate_no_dependencies_removed",
    "version_already_applied",
    # Dependency tree helpers
    "build_vulnerability_parent_map",
    "find_root_parent_for_transitive",
    "parse_dependency_tree",
    # Exclusion helpers
    "apply_exclusions_to_content",
    "parse_exclusions_json",
    # LLM helpers
    "extract_build_content",
    "get_llm",
]
