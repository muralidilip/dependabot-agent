"""Dependency tree parsing helpers."""

from __future__ import annotations

import re


def parse_dependency_tree(dep_tree: str) -> dict[str, list[str]]:
    """Parse Gradle dependency tree to find parent dependencies for each transitive.

    Returns a dict mapping "group:artifact" -> [list of parent "group:artifact" that bring it in]

    Handles various Gradle dependency tree formats:
    - +--- org.springframework.boot:spring-boot:3.2.0
    - |    +--- org.thymeleaf:thymeleaf:3.1.3.RELEASE
    - \\--- org.apache.commons:commons-lang3:3.14.0 (*)
    - +--- org.example:artifact -> 1.2.3
    """
    parents_map: dict[str, list[str]] = {}
    lines = dep_tree.split("\n")

    # Stack to track parent at each indentation level
    # Each item is (indent_level, "group:artifact")
    parent_stack: list[tuple[int, str]] = []

    for line in lines:
        # Skip empty lines
        stripped = line.strip()
        if not stripped:
            continue

        # Check if this line has a dependency (contains --- which indicates tree notation)
        # But also extract the raw content after the tree characters
        if "+---" not in line and "\\---" not in line and "---" not in line:
            continue

        # Calculate indent level by finding the position of the tree marker
        # Lines look like:
        #   "+--- org.springframework.boot:spring-boot:3.2.0"
        #   "|    +--- org.thymeleaf:thymeleaf:3.1.3.RELEASE"
        #   "     \\--- org.apache.commons:commons-lang3:3.14.0"
        marker_pos = max(
            line.find("+---"),
            line.find("\\---"),
            line.find("--- ")  # Fallback for simple format
        )
        if marker_pos < 0:
            continue

        # Calculate level based on marker position
        # Each level is typically 5 characters ("|    " or "     ")
        indent = marker_pos // 5

        # Extract the dependency part (after the ---)
        dep_part = line[marker_pos:].lstrip("+\\- ")
        if ":" not in dep_part:
            continue

        # Clean up the dependency string
        # Remove markers like (*) for resolved duplicates, -> version conflicts
        dep_part = dep_part.split("(")[0].strip()  # Remove (*) and other markers
        dep_part = dep_part.split(" -> ")[0].strip()  # Handle version conflicts

        # Extract group:artifact (may have version or not)
        # Match patterns like: "org.thymeleaf:thymeleaf:3.1.3.RELEASE" or "org.thymeleaf:thymeleaf"
        match = re.search(r'([a-zA-Z0-9._-]+):([a-zA-Z0-9._-]+)', dep_part)
        if not match:
            continue

        dep = f"{match.group(1)}:{match.group(2)}"

        # Pop stack to current level
        while parent_stack and parent_stack[-1][0] >= indent:
            parent_stack.pop()

        # Record this dependency's parent (top of stack)
        if parent_stack:
            parent_dep = parent_stack[-1][1]
            if dep not in parents_map:
                parents_map[dep] = []
            if parent_dep not in parents_map[dep]:
                parents_map[dep].append(parent_dep)

        # Push this dependency onto stack
        parent_stack.append((indent, dep))

    return parents_map


def find_root_parent_for_transitive(
    dep_tree: str,
    transitive_dep: str,
    build_content: str,
    build_system: str
) -> str | None:
    """Find the root (direct) parent dependency that brings in a transitive dep.

    Walks up the dependency tree until it finds a dependency that's in the build file.

    Args:
        dep_tree: The dependency tree output
        transitive_dep: The transitive dependency to find (e.g., "org.thymeleaf:thymeleaf")
        build_content: Current build file content
        build_system: "gradle" or "maven"

    Returns:
        The root parent "group:artifact" that's in the build file, or None if not found.
    """
    from dependabot_agent.helpers.build_file import (
        extract_dependencies_from_gradle,
        extract_dependencies_from_maven,
    )

    parents_map = parse_dependency_tree(dep_tree)

    # Get direct dependencies from build file
    if build_system == "gradle":
        direct_deps = extract_dependencies_from_gradle(build_content)
    else:
        direct_deps = extract_dependencies_from_maven(build_content)

    # BFS to find the root parent that's in the build file
    visited = set()
    queue = [transitive_dep]

    while queue:
        current = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)

        # Check if this dependency is a direct dependency
        if current in direct_deps:
            return current

        # Add parents to queue
        for parent in parents_map.get(current, []):
            if parent not in visited:
                queue.append(parent)

    return None


def build_vulnerability_parent_map(
    alerts: list[dict],
    dep_tree: str,
    build_content: str,
    build_system: str
) -> list[dict]:
    """Build a list of vulnerabilities with their parent dependencies.

    Returns a list of dicts:
    [
        {
            "vulnerable_dep": "org.thymeleaf:thymeleaf",
            "vulnerable_version": "3.1.3.RELEASE",
            "root_parent": "org.springframework.boot:spring-boot-starter-thymeleaf",
            "dependency_chain": ["spring-boot-starter-thymeleaf", "spring-boot-thymeleaf", "thymeleaf-spring6", "thymeleaf"]
        }
    ]
    """
    from dependabot_agent.helpers.build_file import (
        extract_dependencies_from_gradle,
        extract_dependencies_from_maven,
    )

    parents_map = parse_dependency_tree(dep_tree)

    # Get direct dependencies from build file
    if build_system == "gradle":
        direct_deps = extract_dependencies_from_gradle(build_content)
    else:
        direct_deps = extract_dependencies_from_maven(build_content)

    result = []
    seen_deps: set[str] = set()  # Track processed deps to avoid duplicates

    for alert in alerts:
        # Try multiple places where the package name might be stored
        # The normalized alert format uses "package" directly, not nested in "dependency"
        name = alert.get("package", "")

        # Fallback to nested format if not found
        if not name:
            pkg = alert.get("dependency", {}).get("package", {})
            if isinstance(pkg, dict):
                name = pkg.get("name", "")
            elif isinstance(pkg, str):
                name = pkg

        # Also try security_vulnerability path
        if not name:
            vuln = alert.get("security_vulnerability", {})
            vuln_pkg = vuln.get("package", {})
            if isinstance(vuln_pkg, dict):
                name = vuln_pkg.get("name", "")
            elif isinstance(vuln_pkg, str):
                name = vuln_pkg

        # Handle both formats: "thymeleaf" vs "org.thymeleaf:thymeleaf"
        if name and ":" not in name:
            # Try to find a matching dependency in the tree by artifact name
            found = False
            for dep in parents_map.keys():
                if dep.endswith(f":{name}"):
                    name = dep
                    found = True
                    break

            # If not found in parents_map, also check all deps in tree (might be root dep)
            if not found:
                for dep in direct_deps:
                    if dep.endswith(f":{name}"):
                        name = dep
                        break

        if not name or ":" not in name:
            # Still couldn't resolve - add to result with error (but check for duplicates)
            dep_key = name or alert.get("package", "unknown")
            if dep_key not in seen_deps:
                seen_deps.add(dep_key)
                result.append({
                    "vulnerable_dep": dep_key,
                    "vulnerable_version": "",
                    "root_parent": None,
                    "dependency_chain": [],
                    "error": f"Could not resolve full package name: {name}"
                })
            continue

        # Skip if we've already processed this dependency
        if name in seen_deps:
            continue
        seen_deps.add(name)

        # Build the chain from vulnerable dep to root parent
        chain = [name.split(":")[-1]]  # Start with artifact name
        current = name
        root_parent = None

        visited = set()
        while current and current not in visited:
            visited.add(current)

            # Check if this is a direct dependency
            if current in direct_deps:
                root_parent = current
                break

            # Also check by artifact name (in case of group mismatch)
            current_artifact = current.split(":")[-1] if ":" in current else current
            for dep in direct_deps:
                if dep.endswith(f":{current_artifact}"):
                    root_parent = dep
                    break
            if root_parent:
                break

            # Get parents - first try exact match
            parents = parents_map.get(current, [])

            # If not found, try searching by artifact name
            if not parents:
                for dep_key, dep_parents in parents_map.items():
                    if dep_key.endswith(f":{current_artifact}"):
                        parents = dep_parents
                        current = dep_key  # Update current to the found key
                        break

            if not parents:
                break

            # Take first parent (there might be multiple paths)
            parent = parents[0]
            chain.insert(0, parent.split(":")[-1])
            current = parent

        if root_parent:
            result.append({
                "vulnerable_dep": name,
                "vulnerable_version": alert.get("security_vulnerability", {}).get("vulnerable_version_range", ""),
                "root_parent": root_parent,
                "dependency_chain": chain,
            })
        else:
            # Couldn't find via tree traversal - try heuristic matching
            # Common pattern: spring-boot-starter-X brings in X
            artifact_name = name.split(":")[-1] if ":" in name else name

            # Try to find a starter/parent that might bring in this dependency
            inferred_parent = None
            for dep in direct_deps:
                dep_artifact = dep.split(":")[-1] if ":" in dep else dep
                # Check if the direct dep name contains the vulnerable artifact name
                # e.g., "spring-boot-starter-thymeleaf" contains "thymeleaf"
                if artifact_name.lower() in dep_artifact.lower():
                    inferred_parent = dep
                    break
                # Also check for common patterns like "thymeleaf-spring6" -> "spring-boot-starter-thymeleaf"
                base_name = artifact_name.replace("-spring6", "").replace("-spring5", "").replace("-jakarta", "")
                if base_name.lower() in dep_artifact.lower():
                    inferred_parent = dep
                    break

            if inferred_parent:
                result.append({
                    "vulnerable_dep": name,
                    "vulnerable_version": alert.get("security_vulnerability", {}).get("vulnerable_version_range", ""),
                    "root_parent": inferred_parent,
                    "dependency_chain": [inferred_parent.split(":")[-1], artifact_name],
                    "inferred": True  # Mark as heuristically inferred
                })
            else:
                # Truly couldn't find a parent
                result.append({
                    "vulnerable_dep": name,
                    "vulnerable_version": "",
                    "root_parent": None,
                    "dependency_chain": [],
                    "error": "Not found in dependency tree"
                })

    return result

