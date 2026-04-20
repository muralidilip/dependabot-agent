"""Build file manipulation helpers."""

from __future__ import annotations

import re


def extract_dependencies_from_gradle(content: str) -> set[str]:
    """Extract dependency declarations from Gradle build file.

    Returns a set of normalized dependency strings (group:artifact) for comparison.
    """
    deps = set()
    # Match various Gradle dependency formats
    patterns = [
        # implementation 'group:artifact:version' or implementation 'group:artifact'
        r"(?:implementation|api|compileOnly|runtimeOnly|testImplementation|testRuntimeOnly|annotationProcessor|developmentOnly)\s+['\"]([^:]+):([^:'\"]+)",
        # implementation('group:artifact:version') - block format with parentheses
        r"(?:implementation|api|compileOnly|runtimeOnly|testImplementation|testRuntimeOnly|annotationProcessor|developmentOnly)\s*\(\s*['\"]([^:]+):([^:'\"]+)",
        # implementation group: 'x', name: 'y'
        r"group:\s*['\"]([^'\"]+)['\"],\s*name:\s*['\"]([^'\"]+)['\"]",
        # platform("group:artifact:version")
        r"platform\s*\(\s*['\"]([^:]+):([^:'\"]+)",
    ]

    for pattern in patterns:
        for match in re.finditer(pattern, content):
            group_id = match.group(1).strip()
            artifact_id = match.group(2).strip()
            deps.add(f"{group_id}:{artifact_id}")

    return deps


def extract_dependencies_from_maven(content: str) -> set[str]:
    """Extract dependency declarations from Maven POM file.

    Returns a set of normalized dependency strings (group:artifact) for comparison.
    """
    deps = set()
    # Match <groupId>...</groupId> followed by <artifactId>...</artifactId>
    pattern = r"<groupId>([^<]+)</groupId>\s*<artifactId>([^<]+)</artifactId>"
    for match in re.finditer(pattern, content, re.DOTALL):
        group_id = match.group(1).strip()
        artifact_id = match.group(2).strip()
        deps.add(f"{group_id}:{artifact_id}")

    return deps


def validate_no_dependencies_removed(
    original_content: str,
    new_content: str,
    build_system: str
) -> tuple[bool, list[str]]:
    """Validate that no dependencies were removed from the build file.

    Returns (is_valid, list_of_removed_deps).
    """
    if build_system == "gradle":
        original_deps = extract_dependencies_from_gradle(original_content)
        new_deps = extract_dependencies_from_gradle(new_content)
    else:
        original_deps = extract_dependencies_from_maven(original_content)
        new_deps = extract_dependencies_from_maven(new_content)

    removed_deps = original_deps - new_deps

    return len(removed_deps) == 0, list(removed_deps)


def version_already_applied(build_content: str, group_id: str, target_version: str, build_system: str) -> bool:
    """Check if the target version is already present in the build file."""
    if build_system == "gradle":
        # Check plugin versions
        if re.search(rf"['\"]({re.escape(group_id)})['\"].*version\s+['\"]({re.escape(target_version)})['\"]", build_content):
            return True
        # Check dependency versions
        if re.search(rf"{re.escape(group_id)}:[^:]+:{re.escape(target_version)}", build_content):
            return True
    else:  # Maven
        # Check version tags
        if re.search(rf"<version>\s*{re.escape(target_version)}\s*</version>", build_content):
            return True
    return False


def apply_upgrades_to_content(build_content: str, upgrades: list[dict], build_system: str) -> str:
    """Apply version upgrades to build file content programmatically."""
    content = build_content

    for upgrade in upgrades:
        current = upgrade.get("current_version", "")
        target = upgrade.get("target_version", "")
        group_id = upgrade.get("group_id", "")
        artifact_id = upgrade.get("artifact_id", "")

        if not current or not target or current == target:
            continue

        if build_system == "gradle":
            # Handle plugin versions: id 'org.springframework.boot' version '3.2.0'
            if "springframework.boot" in group_id or "spring.dependency-management" in group_id:
                pattern = rf"(id\s+['\"]({re.escape(group_id)})['\"])\s+version\s+['\"]({re.escape(current)})['\"]"
                replacement = rf"\1 version '{target}'"
                content = re.sub(pattern, replacement, content)

            # Handle dependency versions in various formats
            # implementation 'group:artifact:version'
            if artifact_id:
                pattern = rf"(['\"]){re.escape(group_id)}:{re.escape(artifact_id)}:{re.escape(current)}(['\"])"
                replacement = rf"\g<1>{group_id}:{artifact_id}:{target}\g<2>"
                content = re.sub(pattern, replacement, content)

            # Handle platform/BOM versions
            pattern = rf"(platform\s*\(\s*['\"]){re.escape(group_id)}:([^:]+):{re.escape(current)}(['\"])"
            replacement = rf"\g<1>{group_id}:\g<2>:{target}\g<3>"
            content = re.sub(pattern, replacement, content)

        else:  # Maven
            # Handle parent version
            if "parent" in group_id.lower() or "spring-boot-starter-parent" in artifact_id:
                pattern = rf"(<parent>.*?<version>){re.escape(current)}(</version>.*?</parent>)"
                replacement = rf"\g<1>{target}\g<2>"
                content = re.sub(pattern, replacement, content, flags=re.DOTALL)

            # Handle dependency version
            if artifact_id:
                pattern = rf"(<artifactId>{re.escape(artifact_id)}</artifactId>\s*<version>){re.escape(current)}(</version>)"
                replacement = rf"\g<1>{target}\g<2>"
                content = re.sub(pattern, replacement, content)

    return content

