"""LangChain @tool wrappers that the LangGraph agent can invoke."""

from __future__ import annotations

import os
from typing import Any

from langchain_core.tools import tool

from dependabot_agent.tools.build_runner import build_and_test as _build_and_test
from dependabot_agent.tools.build_runner import build_file_path as _build_file_path
from dependabot_agent.tools.build_runner import compile_only as _compile_only
from dependabot_agent.tools.build_runner import detect_build_system as _detect_build_system
from dependabot_agent.tools.build_runner import get_dependency_tree as _get_dependency_tree
from dependabot_agent.tools.build_runner import build_vulnerability_map as _build_vulnerability_map
from dependabot_agent.tools.git_ops import clone_repo as _clone_repo
from dependabot_agent.tools.git_ops import commit_and_push as _commit_and_push
from dependabot_agent.tools.git_ops import create_branch as _create_branch
from dependabot_agent.tools.git_ops import create_pull_request as _create_pull_request
from dependabot_agent.tools.git_ops import revert_file as _revert_file
from dependabot_agent.tools.maven_central import lookup_latest_version as _lookup_latest_version
from dependabot_agent.tools.maven_central import lookup_absolute_latest_version as _lookup_absolute_latest_version


@tool
def clone_repository(repo: str, branch: str = "develop") -> dict[str, str]:
    """Clone a GitHub repository to a local workspace.

    Args:
        repo: owner/repo string, e.g. 'muralidilip/dependabot-test'.
        branch: branch to clone (default: develop, falls back to main).

    Returns a dict with workspace path and build system info.
    """
    owner, repository = repo.strip().split("/", maxsplit=1)
    workspace = _clone_repo(owner, repository, branch)
    build_system = _detect_build_system(workspace)
    bf_path = _build_file_path(workspace)

    with open(bf_path, "r") as f:
        build_content = f.read()

    return {
        "workspace": workspace,
        "build_system": build_system,
        "build_file": bf_path,
        "build_file_content": build_content,
    }


@tool
def read_build_file(workspace: str) -> dict[str, str]:
    """Read the current build file content from the cloned workspace.

    Returns the file path, build system type, and current content.
    """
    bf_path = _build_file_path(workspace)
    with open(bf_path, "r") as f:
        content = f.read()
    return {
        "build_file": bf_path,
        "build_system": _detect_build_system(workspace),
        "content": content,
    }


@tool
def write_build_file(workspace: str, content: str) -> dict[str, str]:
    """Write updated content to the build file (pom.xml or build.gradle).

    Args:
        workspace: the local clone directory.
        content: the full new content for the build file.
    """
    bf_path = _build_file_path(workspace)
    with open(bf_path, "w") as f:
        f.write(content)
    return {"status": "ok", "build_file": bf_path}


@tool
def run_build_and_test(workspace: str, context: str = "") -> dict[str, object]:
    """Run a full clean build + test in the workspace.

    Args:
        workspace: the local clone directory.
        context: Optional context string (e.g., node name) for logging.

    Returns success flag, return code, and truncated stdout/stderr.
    """
    return _build_and_test(workspace, context=context)


@tool
def run_compile_only(workspace: str, context: str = "") -> dict[str, object]:
    """Run only compilation (no tests) to quickly validate dependency changes.

    This is faster than a full build and useful for testing if dependency
    upgrades cause compilation errors before running the full test suite.

    Args:
        workspace: the local clone directory.
        context: Optional context string (e.g., node name) for logging.

    Returns success flag, return code, and truncated stdout/stderr.
    """
    return _compile_only(workspace, context=context)


@tool
def get_dependency_tree(workspace: str, context: str = "") -> dict[str, object]:
    """Get the dependency tree for the project.

    For Gradle: runs 'gradle dependencies --configuration compileClasspath'
    For Maven: runs 'mvn dependency:tree'

    Use this to understand which dependencies are parents of vulnerable
    transitive dependencies, and to verify that upgrades resolved issues.

    Args:
        workspace: the local clone directory.
        context: Optional context string (e.g., node name) for logging.

    Returns success flag and the dependency tree output.
    """
    return _get_dependency_tree(workspace, context=context)


@tool
def build_vulnerability_map(
    workspace: str,
    vulnerable_packages: list[str],
    build_content: str,
    context: str = ""
) -> dict[str, object]:
    """Build a map of parent dependencies to their vulnerable transitive dependencies.

    This is more efficient than parsing the entire dependency tree. It runs
    targeted `dependencyInsight` queries for each vulnerable package to find
    which direct dependency brings it in.

    Args:
        workspace: the local clone directory.
        vulnerable_packages: List of vulnerable packages from Dependabot alerts
                            (e.g., ["org.thymeleaf:thymeleaf", "jackson-databind"])
        build_content: Current build file content (to identify direct deps).
        context: Optional context string for logging.

    Returns:
        {
            "success": True,
            "parent_to_vulns": {
                "spring-boot-starter-thymeleaf": [{"dep": "thymeleaf", "chain": [...]}]
            },
            "direct_upgrades": ["commons-lang3"],  # Just upgrade, no exclusion
            "not_found": ["unknown-dep"],  # Just pin version, no exclusion
        }
    """
    return _build_vulnerability_map(
        workspace,
        vulnerable_packages,
        build_content,
        verbose=False,
        context=context
    )


@tool
def revert_build_file(workspace: str) -> dict[str, str]:
    """Revert the build file in the workspace to its original state from HEAD."""
    bf_path = _build_file_path(workspace)
    _revert_file(workspace, os.path.basename(bf_path))
    return {"status": "reverted", "build_file": bf_path}


@tool
def create_fix_branch(workspace: str, branch_name: str) -> dict[str, str]:
    """Create and checkout a new git branch for the fix.

    Args:
        workspace: the local clone directory.
        branch_name: name for the new branch (e.g. 'fix/dependabot-vulnerabilities').
    """
    _create_branch(workspace, branch_name)
    return {"status": "ok", "branch": branch_name}


@tool
def commit_push_and_pr(
    workspace: str,
    repo: str,
    branch_name: str,
    base_branch: str = "develop",
    commit_message: str = "fix: resolve Dependabot vulnerabilities",
    pr_title: str = "",
    pr_body: str = "",
) -> dict[str, Any]:
    """Commit all changes, push to origin, and open a Pull Request.

    Args:
        workspace: the local clone directory.
        repo: owner/repo string.
        branch_name: the fix branch that was created.
        base_branch: target branch for the PR (default: develop).
        commit_message: git commit message.
        pr_title: PR title (defaults to commit_message).
        pr_body: PR description body.
    """
    _commit_and_push(workspace, branch_name, commit_message)

    owner, repository = repo.strip().split("/", maxsplit=1)
    pr = _create_pull_request(
        owner=owner,
        repo=repository,
        head=branch_name,
        base=base_branch,
        title=pr_title or commit_message,
        body=pr_body,
    )
    return {
        "status": "pr_created",
        "pr_number": pr.get("number"),
        "pr_url": pr.get("html_url"),
    }


@tool
def lookup_maven_version(
    group_id: str,
    artifact_id: str,
    current_version: str,
) -> dict[str, Any]:
    """Look up the latest safe version of a Maven/Gradle dependency on Maven Central.

    Returns the newest version that shares the SAME MAJOR version as
    current_version (i.e. only minor and patch upgrades). Pre-release
    versions (alpha, beta, RC, M, SNAPSHOT) are excluded.

    For Gradle plugins, pass the plugin ID as group_id (artifact_id is
    ignored and resolved automatically):
      • group_id="org.springframework.boot"  → looks up spring-boot-gradle-plugin
      • group_id="io.spring.dependency-management" → looks up dependency-management-plugin

    For regular Maven dependencies, pass exact Maven coordinates:
      • group_id="software.amazon.awssdk", artifact_id="bom"
      • group_id="com.openhtmltopdf", artifact_id="openhtmltopdf-pdfbox"

    Args:
        group_id: Maven groupId or Gradle plugin ID.
        artifact_id: Maven artifactId (ignored for known Gradle plugin IDs).
        current_version: The version currently used in the project.
    """
    return _lookup_latest_version(group_id, artifact_id, current_version)


@tool
def lookup_absolute_latest_maven_version(
    group_id: str,
    artifact_id: str,
) -> dict[str, Any]:
    """Look up the absolute latest stable version of a Maven/Gradle dependency.

    Unlike lookup_maven_version, this does NOT filter by major version.
    Use this when pinning excluded transitive dependencies where the current
    version is unknown.

    Pre-release versions (alpha, beta, RC, M, SNAPSHOT) are excluded.

    Args:
        group_id: Maven groupId (e.g. "org.thymeleaf").
        artifact_id: Maven artifactId (e.g. "thymeleaf").

    Returns:
        dict with group_id, artifact_id, latest_version, and found flag.
    """
    return _lookup_absolute_latest_version(group_id, artifact_id)


