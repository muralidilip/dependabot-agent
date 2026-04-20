"""Exclusion and version pinning helpers for build files."""

from __future__ import annotations

import json
import re


def upgrade_existing_gradle_dependency(content: str, group: str, artifact: str, version: str) -> tuple[str, bool]:
    """Upgrade an existing Gradle dependency to a new version.

    Returns (modified_content, was_upgraded).
    """
    coord = f"{group}:{artifact}"

    # Pattern 1: Simple format with version - implementation 'group:artifact:old_version'
    # Captures the entire dependency declaration and replaces the version
    simple_with_version = rf"((?:implementation|api|compileOnly|runtimeOnly|testImplementation|testRuntimeOnly|developmentOnly)\s+['\"]){re.escape(coord)}:([^'\"]+)(['\"])"

    def replace_version(m):
        prefix = m.group(1)
        suffix = m.group(3)
        return f"{prefix}{coord}:{version}{suffix}"

    new_content, count = re.subn(simple_with_version, replace_version, content)
    if count > 0:
        return new_content, True

    # Pattern 2: Block format with version - implementation('group:artifact:old_version')
    block_with_version = rf"((?:implementation|api|compileOnly|runtimeOnly|testImplementation|testRuntimeOnly|developmentOnly)\s*\(\s*['\"]){re.escape(coord)}:([^'\"]+)(['\"])"

    new_content, count = re.subn(block_with_version, replace_version, content)
    if count > 0:
        return new_content, True

    # Pattern 3: Simple format without version - implementation 'group:artifact'
    # Add the version
    simple_no_version = rf"((?:implementation|api|compileOnly|runtimeOnly|testImplementation|testRuntimeOnly|developmentOnly)\s+)(['\"]){re.escape(coord)}(['\"])"

    def add_version(m):
        prefix = m.group(1)
        quote = m.group(2)
        return f"{prefix}{quote}{coord}:{version}{quote}"

    new_content, count = re.subn(simple_no_version, add_version, content)
    if count > 0:
        return new_content, True

    # Pattern 4: Block format without version - implementation('group:artifact')
    block_no_version = rf"((?:implementation|api|compileOnly|runtimeOnly|testImplementation|testRuntimeOnly|developmentOnly)\s*\(\s*)(['\"]){re.escape(coord)}(['\"])"

    new_content, count = re.subn(block_no_version, add_version, content)
    if count > 0:
        return new_content, True

    return content, False


def upgrade_existing_maven_dependency(content: str, group: str, artifact: str, version: str) -> tuple[str, bool]:
    """Upgrade an existing Maven dependency to a new version.

    Returns (modified_content, was_upgraded).
    """
    # Find dependency with this group and artifact, update or add version
    # Pattern for dependency with version
    dep_with_version = rf'(<dependency>\s*<groupId>{re.escape(group)}</groupId>\s*<artifactId>{re.escape(artifact)}</artifactId>\s*<version>)[^<]+(</version>)'

    new_content, count = re.subn(dep_with_version, rf'\g<1>{version}\g<2>', content, flags=re.DOTALL)
    if count > 0:
        return new_content, True

    # Pattern for dependency without version - add version
    dep_without_version = rf'(<dependency>\s*<groupId>{re.escape(group)}</groupId>\s*<artifactId>{re.escape(artifact)}</artifactId>)(\s*(?:<scope>|</dependency>))'

    def add_version_maven(m):
        prefix = m.group(1)
        suffix = m.group(2)
        return f"{prefix}\n            <version>{version}</version>{suffix}"

    new_content, count = re.subn(dep_without_version, add_version_maven, content, flags=re.DOTALL)
    if count > 0:
        return new_content, True

    return content, False


def apply_exclusions_gradle(
    content: str,
    exclusions: list[dict],
    pins: list[dict],
    skipped: list[dict],
    buildscript_pins: list[dict] | None = None
) -> str:
    """Apply exclusions and pins to a Gradle build file programmatically.

    Args:
        content: Current build.gradle content
        exclusions: List of {parent_group, parent_artifact, excludes: [{group, artifact}]}
        pins: List of {group, artifact, version} to add as direct dependencies
        skipped: List of {group, artifact, reason} for dependencies that couldn't be fixed
        buildscript_pins: List of {group, artifact, version} for buildscript/plugin dependencies
    """
    result = content
    buildscript_pins = buildscript_pins or []

    # Apply exclusions by converting simple deps to block format
    for excl in exclusions:
        parent_group = excl.get("parent_group", "")
        parent_artifact = excl.get("parent_artifact", "")
        excludes = excl.get("excludes", [])

        if not parent_group or not parent_artifact or not excludes:
            continue

        # Build exclusion block
        exclusion_lines = []
        for ex in excludes:
            exclusion_lines.append(f"        exclude group: '{ex.get('group', '')}', module: '{ex.get('artifact', '')}'")
        exclusion_block = "\n".join(exclusion_lines)

        # Pattern 1: Simple format - implementation 'group:artifact' or with version
        # Match: implementation 'org.springframework.boot:spring-boot-starter-webflux'
        simple_pattern = rf"([ \t]*)(implementation|api|compileOnly|runtimeOnly|testImplementation|testRuntimeOnly|developmentOnly)\s+['\"]({re.escape(parent_group)}:{re.escape(parent_artifact)})(:([^'\"]+))?['\"]"

        def replace_simple(m):
            indent = m.group(1)
            config = m.group(2)
            coords = m.group(3)
            version_part = m.group(4) or ""
            return f"{indent}{config}('{coords}{version_part}') {{\n{exclusion_block}\n{indent}}}"

        new_result = re.sub(simple_pattern, replace_simple, result)

        # Only update if we made a change and didn't break anything
        if new_result != result:
            result = new_result
        else:
            # Pattern 2: Already in block format but without exclusions
            # implementation('group:artifact') { ... }
            block_pattern = rf"([ \t]*)(implementation|api|compileOnly|runtimeOnly|testImplementation|testRuntimeOnly|developmentOnly)\s*\(\s*['\"]({re.escape(parent_group)}:{re.escape(parent_artifact)})(:([^'\"]+))?['\"]\s*\)\s*\{{"

            match = re.search(block_pattern, result)
            if match:
                # Find the closing brace of this block and add exclusions before it
                start_pos = match.end()
                brace_count = 1
                pos = start_pos
                while pos < len(result) and brace_count > 0:
                    if result[pos] == '{':
                        brace_count += 1
                    elif result[pos] == '}':
                        brace_count -= 1
                    pos += 1

                # Insert exclusions before the closing brace
                insert_pos = pos - 1
                indent = match.group(1)
                result = result[:insert_pos] + f"\n{exclusion_block}\n{indent}" + result[insert_pos:]

    # Handle pinned dependencies - either upgrade existing or add new
    if pins:
        new_pins = []  # Pins to add as new dependencies
        for pin in pins:
            group = pin.get("group", "")
            artifact = pin.get("artifact", "")
            version = pin.get("version", "")
            if not group or not artifact or not version:
                continue

            coord_with_version = f"{group}:{artifact}:{version}"
            coord_without_version = f"{group}:{artifact}"

            # Skip if exact version already present
            if coord_with_version in result:
                continue

            # Check if dependency already exists (direct dependency)
            already_exists = (
                f"'{coord_without_version}'" in result or
                f'"{coord_without_version}"' in result or
                f"'{coord_without_version}:" in result or
                f'"{coord_without_version}:' in result
            )

            if already_exists:
                # Upgrade existing dependency in-place
                result, was_upgraded = upgrade_existing_gradle_dependency(result, group, artifact, version)
                # If we couldn't upgrade (unlikely), add as new pin
                if not was_upgraded:
                    new_pins.append(pin)
            else:
                # New transitive dependency - add as pin
                new_pins.append(pin)

        # Add any new pins at the end of dependencies block
        if new_pins:
            deps_match = re.search(r'dependencies\s*\{', result)
            if deps_match:
                # Find the closing brace of dependencies block
                deps_start = deps_match.end()
                brace_count = 1
                pos = deps_start
                while pos < len(result) and brace_count > 0:
                    if result[pos] == '{':
                        brace_count += 1
                    elif result[pos] == '}':
                        brace_count -= 1
                    pos += 1

                # Insert pins before the closing brace
                insert_pos = pos - 1
                pin_lines = ["\n    // Pinned dependencies to fix vulnerabilities"]
                for pin in new_pins:
                    group = pin.get("group", "")
                    artifact = pin.get("artifact", "")
                    version = pin.get("version", "")
                    if group and artifact and version:
                        pin_lines.append(f"    implementation '{group}:{artifact}:{version}'")

                if len(pin_lines) > 1:  # More than just the comment
                    result = result[:insert_pos] + '\n'.join(pin_lines) + '\n' + result[insert_pos:]

    # Add skip comments for dependencies that couldn't be fixed
    if skipped:
        skip_comment = "\n// === DEPENDABOT: The following vulnerabilities could not be fixed ===\n"
        for s in skipped:
            skip_comment += f"// DEPENDABOT-SKIP: {s.get('group', '')}:{s.get('artifact', '')} - {s.get('reason', 'unknown')}\n"
        skip_comment += "// ===================================================================\n"

        # Add after plugins block
        plugins_match = re.search(r'plugins\s*\{[^}]*\}', result)
        if plugins_match:
            insert_pos = plugins_match.end()
            result = result[:insert_pos] + "\n" + skip_comment + result[insert_pos:]
        else:
            result = skip_comment + result

    # Add buildscript block with force directives for plugin dependencies
    if buildscript_pins:
        # Check if buildscript block already exists
        buildscript_match = re.search(r'buildscript\s*\{', result)

        # Build the force directives
        force_lines = []
        for pin in buildscript_pins:
            group = pin.get("group", "")
            artifact = pin.get("artifact", "")
            version = pin.get("version", "")
            if group and artifact and version:
                force_lines.append(f"            force '{group}:{artifact}:{version}'")

        if force_lines:
            if buildscript_match:
                # Add to existing buildscript block
                # Find the opening brace and insert after it
                start_pos = buildscript_match.end()

                # Check if configurations.classpath already exists
                classpath_match = re.search(r'configurations\.classpath\s*\{', result[start_pos:])

                if classpath_match:
                    # Add to existing configurations.classpath block
                    classpath_start = start_pos + classpath_match.end()
                    resolution_match = re.search(r'resolutionStrategy\s*\{', result[classpath_start:])

                    if resolution_match:
                        # Add to existing resolutionStrategy block
                        resolution_start = classpath_start + resolution_match.end()
                        insert_content = "\n" + "\n".join(force_lines)
                        result = result[:resolution_start] + insert_content + result[resolution_start:]
                    else:
                        # Add resolutionStrategy block
                        insert_content = f"""
        resolutionStrategy {{
{chr(10).join(force_lines)}
        }}"""
                        result = result[:classpath_start] + insert_content + result[classpath_start:]
                else:
                    # Add configurations.classpath block
                    insert_content = f"""
    configurations.classpath {{
        resolutionStrategy {{
{chr(10).join(force_lines)}
        }}
    }}"""
                    result = result[:start_pos] + insert_content + result[start_pos:]
            else:
                # Add new buildscript block before plugins block
                buildscript_block = f"""
buildscript {{
    configurations.classpath {{
        resolutionStrategy {{
{chr(10).join(force_lines)}
        }}
    }}
}}

"""
                # Find plugins block and insert before it
                plugins_match = re.search(r'plugins\s*\{', result)
                if plugins_match:
                    insert_pos = plugins_match.start()
                    result = result[:insert_pos] + buildscript_block + result[insert_pos:]
                else:
                    # No plugins block, add at the beginning
                    result = buildscript_block + result

    return result


def apply_exclusions_maven(content: str, exclusions: list[dict], pins: list[dict], skipped: list[dict]) -> str:
    """Apply exclusions and pins to a Maven POM file programmatically.

    Args:
        content: Current pom.xml content
        exclusions: List of {parent_group, parent_artifact, excludes: [{group, artifact}]}
        pins: List of {group, artifact, version} to add as direct dependencies
        skipped: List of {group, artifact, reason} for dependencies that couldn't be fixed
    """
    for excl in exclusions:
        parent_group = excl.get("parent_group", "")
        parent_artifact = excl.get("parent_artifact", "")
        excludes = excl.get("excludes", [])

        if not parent_group or not parent_artifact or not excludes:
            continue

        # Find the dependency and add exclusions
        # Pattern to match the dependency block
        dep_pattern = rf'(<dependency>\s*<groupId>{re.escape(parent_group)}</groupId>\s*<artifactId>{re.escape(parent_artifact)}</artifactId>)'

        match = re.search(dep_pattern, content, re.DOTALL)
        if match:
            # Check if exclusions already exist
            dep_end = content.find('</dependency>', match.end())
            dep_block = content[match.start():dep_end + len('</dependency>')]

            if '<exclusions>' not in dep_block:
                # Build exclusions XML
                exclusions_xml = "\n        <exclusions>"
                for ex in excludes:
                    exclusions_xml += f"""
            <exclusion>
                <groupId>{ex.get('group', '')}</groupId>
                <artifactId>{ex.get('artifact', '')}</artifactId>
            </exclusion>"""
                exclusions_xml += "\n        </exclusions>"

                # Insert before </dependency>
                insert_pos = dep_end
                content = content[:insert_pos] + exclusions_xml + content[insert_pos:]

    # Handle pinned dependencies - either upgrade existing or add new
    if pins:
        new_pins = []  # Pins to add as new dependencies
        for pin in pins:
            group = pin.get("group", "")
            artifact = pin.get("artifact", "")
            version = pin.get("version", "")
            if not group or not artifact or not version:
                continue

            # Check if dependency already exists in content
            artifact_exists = f"<artifactId>{artifact}</artifactId>" in content
            group_exists = f"<groupId>{group}</groupId>" in content

            if artifact_exists and group_exists:
                # Upgrade existing dependency in-place
                content, was_upgraded = upgrade_existing_maven_dependency(content, group, artifact, version)
                # If we couldn't upgrade (unlikely), add as new pin
                if not was_upgraded:
                    new_pins.append(pin)
            else:
                # New transitive dependency - add as pin
                new_pins.append(pin)

        # Add any new pins at the end of dependencies block
        if new_pins:
            deps_end = content.rfind('</dependencies>')
            if deps_end > 0:
                pin_xml = "\n        <!-- Pinned dependencies to fix vulnerabilities -->"
                for pin in new_pins:
                    group = pin.get("group", "")
                    artifact = pin.get("artifact", "")
                    version = pin.get("version", "")
                    if group and artifact and version:
                        pin_xml += f"""
        <dependency>
            <groupId>{group}</groupId>
            <artifactId>{artifact}</artifactId>
            <version>{version}</version>
        </dependency>"""

                content = content[:deps_end] + pin_xml + "\n    " + content[deps_end:]

    # Add skip comments
    if skipped:
        skip_comment = "\n    <!-- DEPENDABOT: The following vulnerabilities could not be fixed -->\n"
        for s in skipped:
            skip_comment += f"    <!-- DEPENDABOT-SKIP: {s.get('group', '')}:{s.get('artifact', '')} - {s.get('reason', 'unknown')} -->\n"

        # Add before </dependencies>
        deps_end = content.rfind('</dependencies>')
        if deps_end > 0:
            content = content[:deps_end] + skip_comment + content[deps_end:]

    return content


def apply_exclusions_to_content(
    content: str,
    exclusions: list[dict],
    pins: list[dict],
    skipped: list[dict],
    build_system: str,
    buildscript_pins: list[dict] | None = None
) -> str:
    """Apply exclusions and pins to build file content programmatically.

    This is the safe, deterministic way to modify build files - it cannot
    accidentally remove existing dependencies.

    Args:
        content: Current build file content
        exclusions: List of exclusions for regular dependencies
        pins: List of pins for regular dependencies
        skipped: List of skipped dependencies
        build_system: "gradle" or "maven"
        buildscript_pins: List of pins for buildscript/plugin dependencies (Gradle only)
    """
    if build_system == "gradle":
        return apply_exclusions_gradle(content, exclusions, pins, skipped, buildscript_pins or [])
    else:
        return apply_exclusions_maven(content, exclusions, pins, skipped)


def parse_exclusions_json(response_content: str) -> dict:
    """Parse the LLM's JSON response for exclusions plan."""
    content = response_content.strip()

    # Extract JSON from markdown code blocks if present
    if "```json" in content:
        content = content.split("```json")[1].split("```")[0].strip()
    elif "```" in content:
        parts = content.split("```")
        if len(parts) >= 2:
            content = parts[1].strip()

    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return {"exclusions": [], "pins": [], "skipped": [], "error": "Failed to parse JSON"}

