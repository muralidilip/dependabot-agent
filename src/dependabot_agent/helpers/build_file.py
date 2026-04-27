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


def extract_pinned_dependencies_with_version(content: str, build_system: str) -> list[dict]:
    """Extract explicitly pinned dependency declarations with their versions.

    Returns a list of dicts with group_id, artifact_id, version, and the line that declares it.
    Only returns simple direct dependencies (not platform BOMs, plugins, etc.)
    """
    pins = []

    if build_system == "gradle":
        # Match implementation/api/etc 'group:artifact:version' patterns
        # Use [^:\n] to avoid matching across newlines
        patterns = [
            # implementation 'group:artifact:version'
            (r"(implementation|api|compileOnly|runtimeOnly|testImplementation|developmentOnly)\s+['\"]([^:\n]+):([^:\n]+):([^'\"\n]+)['\"]", False),
            # implementation('group:artifact:version')
            (r"(implementation|api|compileOnly|runtimeOnly|testImplementation|developmentOnly)\s*\(\s*['\"]([^:\n]+):([^:\n]+):([^'\"\n]+)['\"]", False),
        ]

        for pattern, _ in patterns:
            for match in re.finditer(pattern, content):
                config_type = match.group(1)
                group_id = match.group(2).strip()
                artifact_id = match.group(3).strip()
                version = match.group(4).strip()

                # Skip if this looks like a BOM/platform or has exclusions (complex dependency)
                line_start = content.rfind('\n', 0, match.start()) + 1
                line_end = content.find('\n', match.end())
                line = content[line_start:line_end] if line_end > 0 else content[line_start:]

                # Skip if line has exclusion block opening
                if '{' in line and 'exclude' in content[match.end():match.end() + 100].lower():
                    continue

                pins.append({
                    "group_id": group_id,
                    "artifact_id": artifact_id,
                    "version": version,
                    "config_type": config_type,
                    "line": line.strip(),
                    "coord": f"{group_id}:{artifact_id}:{version}",
                })
    else:
        # Maven: find <dependency> blocks with <groupId>, <artifactId>, <version>
        dep_pattern = r"<dependency>\s*<groupId>([^<]+)</groupId>\s*<artifactId>([^<]+)</artifactId>\s*<version>([^<]+)</version>"
        for match in re.finditer(dep_pattern, content, re.DOTALL):
            group_id = match.group(1).strip()
            artifact_id = match.group(2).strip()
            version = match.group(3).strip()

            pins.append({
                "group_id": group_id,
                "artifact_id": artifact_id,
                "version": version,
                "coord": f"{group_id}:{artifact_id}:{version}",
            })

    return pins


def parse_transitive_dependencies(dep_tree: str, build_system: str) -> dict[str, str]:
    """Parse the dependency tree to extract TRANSITIVE dependencies with their resolved versions.

    Only captures nested/indented dependencies (truly transitive), NOT top-level direct dependencies.
    This ensures we don't remove a dependency that only exists because the user declared it.

    Returns a dict mapping 'group:artifact' to resolved version.
    """
    transitive = {}

    if build_system == "gradle":
        # Gradle dependency tree format:
        # +--- org.springframework.boot:spring-boot-starter:3.2.0    <- TOP LEVEL (direct), skip
        # |    +--- org.springframework:spring-core:6.1.0 -> 6.1.2   <- NESTED (transitive), capture
        # |    |    \--- org.springframework:spring-jcl:6.1.2        <- NESTED (transitive), capture
        # +--- com.github.spullara.mustache.java:compiler:0.9.10     <- TOP LEVEL (direct), skip

        for line in dep_tree.split("\n"):
            # Skip non-dependency lines
            if "---" not in line:
                continue

            # Check if this is a nested (transitive) dependency
            # Top-level deps start with "+---" or "\---" at position 0
            # Transitive deps have "|" or spaces before the "+---" or "\---"
            
            # Find where the dependency marker starts
            dash_pos = line.find("+---")
            if dash_pos == -1:
                dash_pos = line.find("\\---")
            if dash_pos == -1:
                continue
            
            # If +--- or \--- is at the start (position 0), it's a top-level dependency
            if dash_pos == 0:
                continue
            
            # Check that there's actual indentation (pipes or spaces before the marker)
            prefix = line[:dash_pos]
            if not prefix.strip():
                # Only whitespace, still could be top-level with weird formatting
                continue

            # This is a nested/transitive dependency (has | or content before +---)
            match = re.search(r'([a-zA-Z0-9._-]+):([a-zA-Z0-9._-]+):([a-zA-Z0-9._-]+)(?:\s*->\s*([a-zA-Z0-9._-]+))?', line)
            if match:
                group_id = match.group(1)
                artifact_id = match.group(2)
                declared_version = match.group(3)
                resolved_version = match.group(4) if match.group(4) else declared_version

                coord = f"{group_id}:{artifact_id}"

                # Always use the resolved version (the one actually used at runtime)
                transitive[coord] = resolved_version
    else:
        # Maven dependency tree format:
        # [INFO] +- org.springframework.boot:spring-boot-starter:jar:3.2.0:compile  <- TOP LEVEL
        # [INFO] |  +- org.springframework:spring-core:jar:6.1.2:compile            <- NESTED

        for line in dep_tree.split("\n"):
            # Skip non-dependency lines
            if ("+-" not in line and "\\-" not in line) or ":" not in line:
                continue

            # Find the position of the dependency marker
            marker_pos = line.find("+-")
            if marker_pos == -1:
                marker_pos = line.find("\\-")
            if marker_pos == -1:
                continue

            # Check what's before the marker - top level has just "[INFO] " before it
            # Nested has "|" characters indicating tree structure
            prefix = line[:marker_pos]
            if "|" not in prefix:
                # No pipe = top-level dependency, skip
                continue

            # Extract dependency coordinate
            match = re.search(r'([a-zA-Z0-9._-]+):([a-zA-Z0-9._-]+):(?:jar|war|pom):([a-zA-Z0-9._-]+)', line)
            if match:
                group_id = match.group(1)
                artifact_id = match.group(2)
                version = match.group(3)

                coord = f"{group_id}:{artifact_id}"
                transitive[coord] = version

    return transitive


def find_redundant_pins(
    build_content: str,
    dep_tree: str,
    build_system: str
) -> list[dict]:
    """Find pinned dependencies that are redundant because they match transitive versions.

    A pin is redundant if:
    1. The same group:artifact is provided transitively in the dependency tree
    2. The transitive version is the SAME as the pinned version
       (We don't remove pins where transitive is higher - user may want to pin down)

    Returns a list of redundant pins with their details.
    """
    pinned = extract_pinned_dependencies_with_version(build_content, build_system)
    transitive = parse_transitive_dependencies(dep_tree, build_system)

    redundant = []

    for pin in pinned:
        coord = f"{pin['group_id']}:{pin['artifact_id']}"

        if coord in transitive:
            transitive_version = transitive[coord]

            # Pin is redundant if transitive provides the exact same version
            if transitive_version == pin["version"]:
                redundant.append({
                    **pin,
                    "transitive_version": transitive_version,
                    "reason": f"Same version {transitive_version} provided transitively",
                })

    return redundant


def remove_redundant_pins_gradle(content: str, redundant_pins: list[dict]) -> str:
    """Remove redundant pinned dependencies from Gradle build file.

    Args:
        content: The build file content
        redundant_pins: List of pins to remove (from find_redundant_pins)

    Returns:
        Updated build file content with redundant pins removed
    """
    result = content

    for pin in redundant_pins:
        group_id = pin["group_id"]
        artifact_id = pin["artifact_id"]
        version = pin["version"]
        config_type = pin.get("config_type", "implementation")

        # Pattern to match the full line with this dependency
        # Handles both quoted styles and with/without parentheses
        patterns = [
            # implementation 'group:artifact:version'
            rf"[ \t]*{config_type}\s+['\"]" + re.escape(f"{group_id}:{artifact_id}:{version}") + r"['\"][ \t]*\n?",
            # implementation('group:artifact:version')
            rf"[ \t]*{config_type}\s*\(\s*['\"]" + re.escape(f"{group_id}:{artifact_id}:{version}") + r"['\"]\s*\)[ \t]*\n?",
        ]

        for pattern in patterns:
            result = re.sub(pattern, "", result)

    # Clean up multiple consecutive blank lines
    result = re.sub(r'\n{3,}', '\n\n', result)

    return result


def remove_redundant_pins_maven(content: str, redundant_pins: list[dict]) -> str:
    """Remove redundant pinned dependencies from Maven POM file.

    Args:
        content: The POM file content
        redundant_pins: List of pins to remove (from find_redundant_pins)

    Returns:
        Updated POM content with redundant pins removed
    """
    result = content

    for pin in redundant_pins:
        group_id = pin["group_id"]
        artifact_id = pin["artifact_id"]
        version = pin["version"]

        # Pattern to match the full <dependency> block
        pattern = (
            r"[ \t]*<dependency>\s*"
            rf"<groupId>{re.escape(group_id)}</groupId>\s*"
            rf"<artifactId>{re.escape(artifact_id)}</artifactId>\s*"
            rf"<version>{re.escape(version)}</version>\s*"
            r"</dependency>[ \t]*\n?"
        )

        result = re.sub(pattern, "", result, flags=re.DOTALL)

    # Clean up multiple consecutive blank lines
    result = re.sub(r'\n{3,}', '\n\n', result)

    return result


def remove_redundant_pins(content: str, redundant_pins: list[dict], build_system: str) -> str:
    """Remove redundant pinned dependencies from build file.

    Args:
        content: The build file content
        redundant_pins: List of pins to remove (from find_redundant_pins)
        build_system: 'gradle' or 'maven'

    Returns:
        Updated build file content with redundant pins removed
    """
    if build_system == "gradle":
        return remove_redundant_pins_gradle(content, redundant_pins)
    else:
        return remove_redundant_pins_maven(content, redundant_pins)


def remove_duplicate_exclusions_gradle(content: str) -> tuple[str, int]:
    """Remove duplicate exclusion entries within each dependency block in Gradle.

    Only removes duplicates within the SAME dependency block - the same exclusion
    in different dependency blocks is kept (each dep may need its own exclusions).

    Args:
        content: The Gradle build file content

    Returns:
        Tuple of (updated content, number of duplicates removed)
    """
    lines = content.split('\n')
    new_lines = []
    current_block_exclusions: set[str] = set()
    in_dependency_block = False
    brace_count = 0
    duplicates_removed = 0

    for line in lines:
        # Track brace depth to know when we exit a dependency block
        line_brace_open = line.count('{') - line.count('}')
        
        # Check if this line starts a dependency block with exclusions
        # e.g., implementation('group:artifact:version') {
        if re.search(
            r'(?:implementation|api|compileOnly|runtimeOnly|testImplementation|testRuntimeOnly|annotationProcessor|developmentOnly)\s*[\(\'"].*\{\s*$',
            line
        ):
            in_dependency_block = True
            current_block_exclusions = set()
            brace_count = 1
            new_lines.append(line)
            continue

        if in_dependency_block:
            brace_count += line_brace_open

            # Check if this is an exclude line
            exclude_match = re.match(
                r'^(\s*)exclude\s+group:\s*[\'"]([^\'"]+)[\'"]\s*,\s*module:\s*[\'"]([^\'"]+)[\'"](.*)$',
                line
            )

            if exclude_match:
                indent = exclude_match.group(1)
                group = exclude_match.group(2)
                module = exclude_match.group(3)
                rest = exclude_match.group(4)

                exclusion_key = f"{group}:{module}"

                if exclusion_key in current_block_exclusions:
                    # This is a duplicate - skip it
                    duplicates_removed += 1
                    continue

                current_block_exclusions.add(exclusion_key)

            # Check if we've exited the dependency block
            if brace_count <= 0:
                in_dependency_block = False
                current_block_exclusions = set()

        new_lines.append(line)

    return '\n'.join(new_lines), duplicates_removed


def remove_duplicate_exclusions_maven(content: str) -> tuple[str, int]:
    """Remove duplicate exclusion entries within each dependency block in Maven.

    Only removes duplicates within the SAME <dependency> block.

    Args:
        content: The Maven POM file content

    Returns:
        Tuple of (updated content, number of duplicates removed)
    """
    duplicates_removed = 0

    # Find all dependency blocks with exclusions
    dep_pattern = re.compile(r'(<dependency>.*?</dependency>)', re.DOTALL)

    def process_dependency(match: re.Match) -> str:
        nonlocal duplicates_removed
        dep_block = match.group(1)

        # Find all exclusions in this block
        exclusions_pattern = re.compile(
            r'<exclusion>\s*<groupId>([^<]+)</groupId>\s*<artifactId>([^<]+)</artifactId>\s*</exclusion>',
            re.DOTALL
        )

        seen_exclusions: set[str] = set()
        result_parts: list[str] = []
        last_end = 0

        for excl_match in exclusions_pattern.finditer(dep_block):
            group_id = excl_match.group(1).strip()
            artifact_id = excl_match.group(2).strip()
            exclusion_key = f"{group_id}:{artifact_id}"

            # Add content before this exclusion
            result_parts.append(dep_block[last_end:excl_match.start()])

            if exclusion_key in seen_exclusions:
                # Duplicate - skip it
                duplicates_removed += 1
                # Remove any preceding whitespace/newline for cleaner output
                if result_parts and result_parts[-1].endswith('\n'):
                    result_parts[-1] = result_parts[-1].rstrip('\n \t')
                    if result_parts[-1] and not result_parts[-1].endswith('\n'):
                        result_parts[-1] += '\n'
            else:
                seen_exclusions.add(exclusion_key)
                result_parts.append(excl_match.group(0))

            last_end = excl_match.end()

        result_parts.append(dep_block[last_end:])
        return ''.join(result_parts)

    result = dep_pattern.sub(process_dependency, content)
    return result, duplicates_removed


def remove_duplicate_exclusions(content: str, build_system: str) -> tuple[str, int]:
    """Remove duplicate exclusion entries from build file.

    Only removes duplicates within the SAME dependency block - the same exclusion
    in different dependency blocks is kept (each dep needs its own exclusions).

    Args:
        content: The build file content
        build_system: 'gradle' or 'maven'

    Returns:
        Tuple of (updated content, number of duplicates removed)
    """
    if build_system == "gradle":
        return remove_duplicate_exclusions_gradle(content)
    else:
        return remove_duplicate_exclusions_maven(content)


def find_all_transitive_occurrences(
    dep_tree: str,
    group_id: str,
    artifact_id: str,
    build_system: str
) -> list[dict]:
    """Find all transitive occurrences of a dependency in the tree.

    Searches the dependency tree for ALL places where a given group:artifact
    appears as a TRANSITIVE dependency (not top-level).

    Args:
        dep_tree: The raw dependency tree output
        group_id: The group ID to search for
        artifact_id: The artifact ID to search for
        build_system: "gradle" or "maven"

    Returns:
        List of occurrences, each with:
        - parent: The immediate parent dependency (group:artifact)
        - version: The resolved version at this occurrence
        - declared_version: The originally declared version (before any forcing)
        - depth: How deep in the tree (1 = direct child of root dep)
        - path: Full path from root to this occurrence
    """
    from dependabot_agent.helpers.dep_tree import parse_dependency_tree

    target_coord = f"{group_id}:{artifact_id}"
    occurrences = []

    if build_system == "gradle":
        # Parse the tree to get parent relationships
        parents_map = parse_dependency_tree(dep_tree)

        # Track current path through tree for each occurrence
        lines = dep_tree.split("\n")

        # Stack to track path: [(indent_level, "group:artifact:version")]
        path_stack: list[tuple[int, str]] = []

        for line in lines:
            if "---" not in line:
                continue

            # Find indent level
            dash_pos = line.find("+---")
            if dash_pos == -1:
                dash_pos = line.find("\\---")
            if dash_pos == -1:
                continue

            # Calculate depth (each level is ~5 chars)
            depth = dash_pos // 5

            # Extract dependency info
            dep_part = line[dash_pos:].lstrip("+\\- ")
            if ":" not in dep_part:
                continue

            # Clean up - remove (*) and other markers
            dep_part = dep_part.split("(")[0].strip()

            # Handle version override: group:artifact:declared -> resolved
            version = ""
            declared_version = ""
            match = re.search(
                r'([a-zA-Z0-9._-]+):([a-zA-Z0-9._-]+):([a-zA-Z0-9._-]+)(?:\s*->\s*([a-zA-Z0-9._-]+))?',
                dep_part
            )
            if match:
                g = match.group(1)
                a = match.group(2)
                declared_version = match.group(3)
                resolved_version = match.group(4) if match.group(4) else declared_version
                version = resolved_version
                coord = f"{g}:{a}"
            else:
                # Try simpler pattern without version
                match = re.search(r'([a-zA-Z0-9._-]+):([a-zA-Z0-9._-]+)', dep_part)
                if match:
                    g = match.group(1)
                    a = match.group(2)
                    coord = f"{g}:{a}"
                else:
                    continue

            # Update path stack - pop back to current depth
            while path_stack and path_stack[-1][0] >= depth:
                path_stack.pop()

            # Check if this is our target AND it's transitive (depth > 0)
            if coord == target_coord and depth > 0:
                # Build the path from stack
                path = [p[1] for p in path_stack] + [f"{coord}:{version}" if version else coord]
                parent = path_stack[-1][1] if path_stack else None

                if parent:
                    # Extract just group:artifact from parent (may have version)
                    parent_match = re.match(r'([^:]+:[^:]+)', parent)
                    parent_coord = parent_match.group(1) if parent_match else parent

                    occurrences.append({
                        "parent": parent_coord,
                        "version": version,
                        "declared_version": declared_version,
                        "depth": depth,
                        "path": path,
                    })

            # Push current dep onto stack
            path_stack.append((depth, f"{coord}:{version}" if version else coord))

    else:  # Maven
        lines = dep_tree.split("\n")
        path_stack: list[tuple[int, str]] = []

        for line in lines:
            if ("+-" not in line and "\\-" not in line) or ":" not in line:
                continue

            # Find marker position
            marker_pos = line.find("+-")
            if marker_pos == -1:
                marker_pos = line.find("\\-")
            if marker_pos == -1:
                continue

            # Calculate depth based on prefix
            prefix = line[:marker_pos]
            depth = prefix.count("|") + (1 if "|" in prefix or marker_pos > 10 else 0)

            # Extract dependency info
            match = re.search(
                r'([a-zA-Z0-9._-]+):([a-zA-Z0-9._-]+):(?:jar|war|pom):([a-zA-Z0-9._-]+)',
                line
            )
            if not match:
                continue

            g = match.group(1)
            a = match.group(2)
            version = match.group(3)
            coord = f"{g}:{a}"

            # Update path stack
            while path_stack and path_stack[-1][0] >= depth:
                path_stack.pop()

            # Check if this is our target AND it's transitive (has | in prefix)
            if coord == target_coord and "|" in prefix:
                path = [p[1] for p in path_stack] + [f"{coord}:{version}"]
                parent = path_stack[-1][1] if path_stack else None

                if parent:
                    parent_match = re.match(r'([^:]+:[^:]+)', parent)
                    parent_coord = parent_match.group(1) if parent_match else parent

                    occurrences.append({
                        "parent": parent_coord,
                        "version": version,
                        "declared_version": version,  # Maven doesn't have overrides like Gradle
                        "depth": depth,
                        "path": path,
                    })

            path_stack.append((depth, f"{coord}:{version}"))

    return occurrences


def analyze_pins_for_cleanup(
    build_content: str,
    dep_tree: str,
    build_system: str
) -> list[dict]:
    """Analyze all pinned dependencies to determine cleanup eligibility.

    For each pin, finds all transitive occurrences and classifies it as:
    - "direct_only": Pin has NO transitive occurrences - must be kept
    - "redundant": Pin matches transitive version - can be removed (was vuln fix)
    - "version_forcing": Pin forces different version than transitive - must be kept

    Args:
        build_content: Current build file content
        dep_tree: Raw dependency tree output
        build_system: "gradle" or "maven"

    Returns:
        List of analyzed pins with classification and transitive context
    """
    pins = extract_pinned_dependencies_with_version(build_content, build_system)
    analyzed = []

    for pin in pins:
        group_id = pin["group_id"]
        artifact_id = pin["artifact_id"]
        pinned_version = pin["version"]

        # Find all transitive occurrences
        occurrences = find_all_transitive_occurrences(
            dep_tree, group_id, artifact_id, build_system
        )

        if not occurrences:
            # No transitive occurrences - this is a direct-only dependency
            classification = "direct_only"
            reason = "Pin has no transitive occurrences - it's a direct dependency only"
        else:
            # Check both resolved and declared versions
            # resolved_version: what actually gets used
            # declared_version: what the transitive would be without forcing
            resolved_versions = {occ["version"] for occ in occurrences if occ.get("version")}
            declared_versions = {occ.get("declared_version", occ["version"]) for occ in occurrences if occ.get("version")}

            if not resolved_versions:
                # No versions found (unusual) - be safe and keep
                classification = "direct_only"
                reason = "Could not determine transitive versions"
            elif declared_versions != resolved_versions:
                # Version override detected (declared -> resolved)
                # If pin matches resolved but not declared, the pin is forcing
                if pinned_version in resolved_versions and pinned_version not in declared_versions:
                    classification = "version_forcing"
                    dv_str = ", ".join(sorted(declared_versions))
                    reason = f"Pin forces {pinned_version}, transitive would be {dv_str}"
                elif pinned_version not in resolved_versions:
                    # Pin doesn't match resolved - forcing a different version
                    classification = "version_forcing"
                    rv_str = ", ".join(sorted(resolved_versions))
                    reason = f"Pin forces {pinned_version}, transitive resolves to {rv_str}"
                else:
                    # Unusual case - treat as version forcing to be safe
                    classification = "version_forcing"
                    reason = f"Version override detected - keeping pin for safety"
            elif len(resolved_versions) == 1 and pinned_version in resolved_versions:
                # Pin matches the single transitive version AND no override - redundant
                classification = "redundant"
                reason = f"Transitive provides same version {pinned_version} - pin is redundant"
            elif pinned_version in resolved_versions:
                # Pin matches one of multiple transitive versions - could be redundant
                classification = "redundant"
                reason = f"Transitive provides same version {pinned_version} - pin is redundant"
            else:
                # Pin forces a different version
                classification = "version_forcing"
                rv_str = ", ".join(sorted(resolved_versions))
                reason = f"Pin forces {pinned_version}, transitive has {rv_str}"

        analyzed.append({
            **pin,
            "transitive_occurrences": occurrences,
            "classification": classification,
            "reason": reason,
        })

    return analyzed

