"""Build and test runner for Maven and Gradle projects."""

from __future__ import annotations

import os
import stat
import subprocess
import sys


def _run(
    cmd: list[str], cwd: str, timeout: int = 600, verbose: bool = True
) -> subprocess.CompletedProcess[str]:
    """Run a command and optionally stream output to console in real-time."""
    if verbose:
        print(f"\n{'─'*40}", file=sys.stderr)
        print(f"{'='*60}", file=sys.stderr)
        print(f"Running: {' '.join(cmd)}", file=sys.stderr)
        print(f"Working directory: {cwd}", file=sys.stderr)
        print(f"{'='*60}\n", file=sys.stderr)

        # Stream output in real-time
        process = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        stdout_lines = []
        try:
            for line in process.stdout:
                print(line, end='', file=sys.stderr)
                stdout_lines.append(line)
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

        stdout_output = ''.join(stdout_lines)

        print(f"\n{'='*60}", file=sys.stderr)
        print(f"Build finished with return code: {process.returncode}", file=sys.stderr)
        print(f"{'='*60}\n", file=sys.stderr)

        return subprocess.CompletedProcess(
            args=cmd,
            returncode=process.returncode,
            stdout=stdout_output,
            stderr=""
        )
    else:
        return subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False
        )


def _ensure_executable(path: str) -> None:
    """Make a file executable (chmod +x) if it exists but lacks the execute bit."""
    if os.path.isfile(path):
        st = os.stat(path)
        if not (st.st_mode & stat.S_IXUSR):
            os.chmod(path, st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def detect_build_system(workspace: str) -> str:
    """Return 'gradle' or 'maven' based on which build file exists."""
    if os.path.isfile(os.path.join(workspace, "build.gradle")) or os.path.isfile(
        os.path.join(workspace, "build.gradle.kts")
    ):
        return "gradle"
    if os.path.isfile(os.path.join(workspace, "pom.xml")):
        return "maven"
    raise FileNotFoundError("No pom.xml or build.gradle found in " + workspace)


def build_file_path(workspace: str) -> str:
    """Return the absolute path of the primary build file."""
    for name in ("build.gradle", "build.gradle.kts", "pom.xml"):
        path = os.path.join(workspace, name)
        if os.path.isfile(path):
            return path
    raise FileNotFoundError("No build file found in " + workspace)


def build_and_test(workspace: str, build_system: str, verbose: bool = True) -> dict[str, object]:
    """Run a full build + tests and return success status and output.
    
    Args:
        workspace: Path to the project workspace.
        build_system: The build system type ('gradle' or 'maven').
        verbose: If True, stream build output to stderr in real-time.
    """
    if build_system == "gradle":
        # Use the wrapper if available
        wrapper = os.path.join(workspace, "gradlew")
        _ensure_executable(wrapper)
        cmd_prefix = [wrapper] if os.path.isfile(wrapper) else ["gradle"]
        result = _run(cmd_prefix + ["clean", "build", "--no-daemon", "--stacktrace"], cwd=workspace, verbose=verbose)
    else:
        wrapper = os.path.join(workspace, "mvnw")
        _ensure_executable(wrapper)
        cmd_prefix = [wrapper] if os.path.isfile(wrapper) else ["mvn"]
        result = _run(cmd_prefix + ["clean", "verify", "-B", "-e"], cwd=workspace, verbose=verbose)

    return {
        "success": result.returncode == 0,
        "return_code": result.returncode,
        "stdout": _truncate(result.stdout, 8000),
        "stderr": _truncate(result.stderr, 8000),
    }


def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len] + "\n... (truncated)"


def get_dependency_tree(
    workspace: str,
    build_system: str,
    alerts: list[dict] | None = None,
    verbose: bool = True
) -> dict[str, object]:
    """Get the dependency tree for the project, filtered to show only vulnerable dependencies.

    Args:
        workspace: Path to the project workspace.
        build_system: The build system type ('gradle' or 'maven').
        alerts: List of Dependabot alerts. If provided, returns only vulnerable deps with their parents.
        verbose: If True, stream output to stderr in real-time.

    Returns:
        Dict with:
        - success: bool
        - vulnerability_parents: Dict mapping "group:artifact:version" -> [list of root parent coords]
        - raw_tree: The raw dependency tree output (truncated)
    """
    import re

    vulnerability_parents: dict[str, list[str]] = {}
    combined_tree = ""
    success = False
    return_code = 1
    stderr = ""

    if build_system == "gradle":
        wrapper = os.path.join(workspace, "gradlew")
        _ensure_executable(wrapper)
        cmd_prefix = [wrapper] if os.path.isfile(wrapper) else ["gradle"]

        # Get all configurations dependency tree
        result = _run(
            cmd_prefix + ["dependencies", "--no-daemon"],
            cwd=workspace,
            verbose=verbose
        )

        all_tree = result.stdout or ""
        success = result.returncode == 0
        return_code = result.returncode
        stderr = result.stderr or ""

        # Also get buildEnvironment (build script/plugin dependencies)
        result_build_env = _run(
            cmd_prefix + ["buildEnvironment", "--no-daemon"],
            cwd=workspace,
            verbose=verbose
        )

        build_env_tree = result_build_env.stdout or ""

        # Combine both trees for parsing
        combined_tree = all_tree
        if build_env_tree:
            combined_tree += "\n\n" + "=" * 60 + "\n"
            combined_tree += "BUILD ENVIRONMENT (Plugin Dependencies)\n"
            combined_tree += "=" * 60 + "\n\n"
            combined_tree += build_env_tree

        # If alerts provided, extract only vulnerability info
        if alerts:
            vulnerability_parents = _extract_vulnerability_parents(
                combined_tree, alerts, build_system
            )

    else:  # Maven
        wrapper = os.path.join(workspace, "mvnw")
        _ensure_executable(wrapper)
        cmd_prefix = [wrapper] if os.path.isfile(wrapper) else ["mvn"]

        # Get full dependency tree
        result = _run(
            cmd_prefix + ["dependency:tree", "-B"],
            cwd=workspace,
            verbose=verbose
        )

        combined_tree = result.stdout or ""
        success = result.returncode == 0
        return_code = result.returncode
        stderr = result.stderr or ""

        # If alerts provided, extract only vulnerability info
        if alerts:
            vulnerability_parents = _extract_vulnerability_parents(
                combined_tree, alerts, build_system
            )

    return {
        "success": success,
        "return_code": return_code,
        "vulnerability_parents": vulnerability_parents,
        "raw_tree": _truncate(combined_tree, 30000),
        "stderr": _truncate(stderr, 3000),
    }


def _extract_vulnerability_parents(
    dep_tree: str,
    alerts: list[dict],
    build_system: str
) -> dict[str, list[str]]:
    """Extract vulnerable dependencies and their root parents from dependency tree.

    Args:
        dep_tree: The raw dependency tree output
        alerts: List of Dependabot alerts with 'package' and optionally 'first_patched_version'
        build_system: 'gradle' or 'maven'

    Returns:
        Dict mapping "group:artifact:version" -> [list of root parent coordinates]
        e.g., {"tools.jackson.core:jackson-core:3.1.0": ["org.springframework.boot:spring-boot-starter-webflux:4.0.5"]}
    """
    import re

    vulnerability_parents: dict[str, list[str]] = {}

    # Extract vulnerable package names from alerts
    vulnerable_packages: set[str] = set()
    for alert in alerts:
        pkg = alert.get("package", "")
        if pkg and ":" in pkg:
            vulnerable_packages.add(pkg)

    if not vulnerable_packages:
        return vulnerability_parents

    lines = dep_tree.split("\n")

    if build_system == "gradle":
        # Parse Gradle dependency tree format
        # Track the hierarchy using indentation
        # +--- org.springframework.boot:spring-boot-starter-mongodb -> 4.0.0  (BOM-managed, no declared version)
        # |    +--- org.springframework.boot:spring-boot-starter:4.0.0
        # |    |    +--- org.springframework.boot:spring-boot-starter-logging:4.0.0
        # |    |    |    +--- ch.qos.logback:logback-classic:1.5.21
        # |    |    |    |    +--- ch.qos.logback:logback-core:1.5.21
        #
        # Skip special Gradle markers:
        # - (c) = constraint (from BOM, not a real dependency)
        # - (*) = duplicate (already shown elsewhere)
        # - (n) = not resolved

        # Stack to track parent hierarchy: [(depth, coordinate)]
        parent_stack: list[tuple[int, str]] = []

        for line in lines:
            # Skip non-dependency lines
            if "+---" not in line and "\\---" not in line:
                continue

            # Skip constraint entries (c), duplicates (*), and not-resolved (n)
            # These are not real dependencies that bring in transitives
            if "(c)" in line or "(*)" in line or "(n)" in line:
                continue

            # Calculate depth based on position of the marker
            marker_pos = line.find("+---")
            if marker_pos == -1:
                marker_pos = line.find("\\---")
            if marker_pos == -1:
                continue

            depth = marker_pos

            # Extract the dependency coordinate
            # Handle multiple formats:
            # 1. group:artifact:version
            # 2. group:artifact:version -> resolved_version
            # 3. group:artifact -> resolved_version (BOM-managed, no declared version)
            
            # First try: group:artifact:declared_version -> resolved_version
            match = re.search(r'([a-zA-Z0-9._-]+):([a-zA-Z0-9._-]+):([a-zA-Z0-9._-]+)\s*->\s*([a-zA-Z0-9._-]+)', line)
            if match:
                group_id = match.group(1)
                artifact_id = match.group(2)
                resolved_version = match.group(4)  # Use resolved version
            else:
                # Second try: group:artifact -> resolved_version (BOM-managed)
                match = re.search(r'([a-zA-Z0-9._-]+):([a-zA-Z0-9._-]+)\s*->\s*([a-zA-Z0-9._-]+)', line)
                if match:
                    group_id = match.group(1)
                    artifact_id = match.group(2)
                    resolved_version = match.group(3)
                else:
                    # Third try: group:artifact:version (no override)
                    match = re.search(r'([a-zA-Z0-9._-]+):([a-zA-Z0-9._-]+):([a-zA-Z0-9._-]+)', line)
                    if match:
                        group_id = match.group(1)
                        artifact_id = match.group(2)
                        resolved_version = match.group(3)
                    else:
                        continue

            coord = f"{group_id}:{artifact_id}"
            full_coord = f"{group_id}:{artifact_id}:{resolved_version}"

            # Pop parents that are at same or greater depth (siblings or uncles)
            while parent_stack and parent_stack[-1][0] >= depth:
                parent_stack.pop()

            # Check if this is a vulnerable package
            if coord in vulnerable_packages:
                # Find root parent (first item in stack, or self if top-level)
                if parent_stack:
                    root_parent = parent_stack[0][1]
                    if full_coord not in vulnerability_parents:
                        vulnerability_parents[full_coord] = []
                    if root_parent not in vulnerability_parents[full_coord]:
                        vulnerability_parents[full_coord].append(root_parent)
                else:
                    # This is a top-level (direct) dependency
                    if full_coord not in vulnerability_parents:
                        vulnerability_parents[full_coord] = []
                    if "DIRECT" not in vulnerability_parents[full_coord]:
                        vulnerability_parents[full_coord].append("DIRECT")

            # Push this dependency onto the stack
            parent_stack.append((depth, full_coord))

    else:
        # Maven dependency tree format
        # [INFO] +- org.springframework.boot:spring-boot-starter-webflux:jar:4.0.5:compile
        # [INFO] |  +- org.springframework.boot:spring-boot-starter-json:jar:4.0.5:compile
        # [INFO] |  |  +- tools.jackson.core:jackson-databind:jar:3.1.0:compile

        parent_stack: list[tuple[int, str]] = []

        for line in lines:
            # Skip non-dependency lines
            if ("+-" not in line and "\\-" not in line) or ":" not in line:
                continue

            # Find the marker position
            marker_pos = line.find("+-")
            if marker_pos == -1:
                marker_pos = line.find("\\-")
            if marker_pos == -1:
                continue

            depth = marker_pos

            # Extract Maven coordinate: group:artifact:packaging:version:scope
            match = re.search(r'([a-zA-Z0-9._-]+):([a-zA-Z0-9._-]+):(?:jar|war|pom):([a-zA-Z0-9._-]+)', line)
            if not match:
                continue

            group_id = match.group(1)
            artifact_id = match.group(2)
            version = match.group(3)

            coord = f"{group_id}:{artifact_id}"
            full_coord = f"{group_id}:{artifact_id}:{version}"

            # Pop parents at same or greater depth
            while parent_stack and parent_stack[-1][0] >= depth:
                parent_stack.pop()

            # Check if vulnerable
            if coord in vulnerable_packages:
                if parent_stack:
                    root_parent = parent_stack[0][1]
                    if full_coord not in vulnerability_parents:
                        vulnerability_parents[full_coord] = []
                    if root_parent not in vulnerability_parents[full_coord]:
                        vulnerability_parents[full_coord].append(root_parent)
                else:
                    if full_coord not in vulnerability_parents:
                        vulnerability_parents[full_coord] = []
                    if "DIRECT" not in vulnerability_parents[full_coord]:
                        vulnerability_parents[full_coord].append("DIRECT")

            parent_stack.append((depth, full_coord))

    return vulnerability_parents


def build_vulnerability_map(
    workspace: str,
    vulnerable_packages: list[str],
    build_content: str,
    build_system: str,
    verbose: bool = False
) -> dict[str, object]:
    """Build a map of parent dependencies to their vulnerable transitive dependencies.

    Instead of parsing the entire dependency tree, this function:
    1. Takes a list of vulnerable packages from Dependabot alerts
    2. Runs `dependencyInsight` for each to find its parent chain
    3. Finds the root parent (direct dep in build file) for each
    4. Returns a structured map for applying exclusions/pins

    Strategy:
    - If a vulnerability is found as a TRANSITIVE dependency in the tree,
      we add an exclusion to its parent AND pin the safe version.
    - If a vulnerability is found in buildEnvironment (plugin dependency),
      we add buildscript exclusion/force.
    - If a vulnerability is NOT found in the tree (truly direct dependency
      or already excluded), we just upgrade/pin the version.

    Args:
        workspace: Path to the project workspace.
        vulnerable_packages: List of vulnerable packages (e.g., ["org.thymeleaf:thymeleaf"])
        build_content: Current build file content (to identify direct deps)
        build_system: The build system type ('gradle' or 'maven')
        verbose: If True, stream output to stderr.

    Returns:
        {
            "success": True,
            "parent_to_vulns": {
                "org.springframework.boot:spring-boot-starter-thymeleaf": [
                    {"dep": "org.thymeleaf:thymeleaf", "chain": [...], "source": "compileClasspath"}
                ]
            },
            "buildscript_vulns": ["tools.jackson.core:jackson-core"],  # Found in buildEnvironment
            "direct_upgrades": ["org.apache.commons:commons-lang3"],  # Not found in tree as transitive
            "not_found": ["some.unknown:dep"],  # Couldn't determine, just pin version
        }
    """
    import re

    # Extract direct dependencies from build file (for finding root parent in chain)
    direct_deps = _extract_direct_deps(build_content, build_system)

    parent_to_vulns: dict[str, list[dict]] = {}
    buildscript_vulns: list[str] = []  # Vulnerabilities from buildEnvironment (plugins)
    direct_upgrades: list[str] = []
    not_found: list[str] = []

    for pkg in vulnerable_packages:
        if not pkg or ":" not in pkg:
            continue

        # ALWAYS run dependency insight to find if it's a transitive dependency
        # Don't skip based on whether it's in the build file - it might be a previous pin
        insight = _get_dependency_insight(workspace, pkg, build_system, verbose)

        if not insight["success"] or not insight["parent_chain"]:
            # Dependency not found in tree - could be:
            # 1. A truly direct dependency (not transitive)
            # 2. Already excluded
            # 3. Not in this configuration
            # In all cases, just upgrade/pin without exclusion
            if pkg in direct_deps:
                direct_upgrades.append(pkg)
            else:
                # Try heuristic matching first
                parent = _find_parent_heuristic(pkg, direct_deps)
                if parent:
                    if parent not in parent_to_vulns:
                        parent_to_vulns[parent] = []
                    parent_to_vulns[parent].append({
                        "dep": pkg,
                        "chain": [],
                        "inferred": True,
                        "source": None
                    })
                else:
                    not_found.append(pkg)
            continue

        # Found in dependency tree - extract the parent chain and source
        chain = insight["parent_chain"]
        source = insight.get("source")

        # Check if this is a buildEnvironment (plugin) dependency
        if source == "buildEnvironment":
            # Plugin dependency - needs buildscript exclusion
            buildscript_vulns.append(pkg)
            continue

        # Find the root parent in the chain that's a direct dependency
        # Skip the vulnerable package itself and any previous pins
        root_parent = None
        for dep in chain:
            # Skip if this is the vulnerable package itself
            if dep == pkg:
                continue
            # Skip if this looks like a pin of the vulnerable package
            # (e.g., jackson-core appearing as both transitive and pinned)
            if dep.split(":")[1] == pkg.split(":")[1]:
                continue
            # Found a direct dependency that brings in this vulnerability
            if dep in direct_deps:
                root_parent = dep
                break

        if root_parent:
            # This is a transitive dependency - we need to exclude it from parent
            if root_parent not in parent_to_vulns:
                parent_to_vulns[root_parent] = []
            parent_to_vulns[root_parent].append({
                "dep": pkg,
                "chain": chain,
                "inferred": False,
                "source": source
            })
        else:
            # Chain found but no suitable direct dep parent
            # This means the vulnerable dep is being brought in directly
            # (either as a true direct dep or we can't trace the parent)
            if pkg in direct_deps:
                direct_upgrades.append(pkg)
            else:
                # Try heuristic matching
                parent = _find_parent_heuristic(pkg, direct_deps)
                if parent:
                    if parent not in parent_to_vulns:
                        parent_to_vulns[parent] = []
                    parent_to_vulns[parent].append({
                        "dep": pkg,
                        "chain": chain,
                        "inferred": True,
                        "source": source
                    })
                else:
                    not_found.append(pkg)

    return {
        "success": True,
        "parent_to_vulns": parent_to_vulns,
        "buildscript_vulns": buildscript_vulns,
        "direct_upgrades": direct_upgrades,
        "not_found": not_found,
    }


def _get_dependency_insight(
    workspace: str,
    dependency: str,
    build_system: str,
    verbose: bool = False
) -> dict[str, object]:
    """Get dependency insight for a specific dependency.

    For Gradle, checks both compileClasspath and buildEnvironment configurations
    to find transitive dependencies in both project dependencies and build script dependencies.

    Returns:
        {
            "success": bool,
            "parent_chain": list[str],
            "source": "compileClasspath" | "buildEnvironment" | "runtimeClasspath" | None,
            "raw_output": str
        }
    """
    import re

    parts = dependency.split(":")
    if len(parts) < 2:
        return {"success": False, "parent_chain": [], "source": None}

    artifact_id = parts[1]
    group_id = parts[0]

    if build_system == "gradle":
        wrapper = os.path.join(workspace, "gradlew")
        _ensure_executable(wrapper)
        cmd_prefix = [wrapper] if os.path.isfile(wrapper) else ["gradle"]

        # First try compileClasspath (project dependencies)
        result = _run(
            cmd_prefix + [
                "dependencyInsight",
                "--dependency", artifact_id,
                "--configuration", "compileClasspath",
                "--no-daemon"
            ],
            cwd=workspace,
            verbose=verbose,
            timeout=120
        )

        # Parse the output to extract parent chain
        chain = _parse_insight_output(result.stdout, dependency, build_system)
        source = "compileClasspath" if chain and len(chain) > 1 else None

        # If not found in compileClasspath, try buildEnvironment (build script dependencies)
        if not chain or len(chain) <= 1:
            result_build_env = _run(
                cmd_prefix + [
                    "buildEnvironment",
                    "--no-daemon"
                ],
                cwd=workspace,
                verbose=verbose,
                timeout=120
            )

            # Parse buildEnvironment output for this dependency
            chain_build_env = _parse_build_environment_output(result_build_env.stdout, dependency)
            if chain_build_env and len(chain_build_env) > len(chain):
                chain = chain_build_env
                result = result_build_env
                source = "buildEnvironment"

        # Also try runtimeClasspath if still not found
        if not chain or len(chain) <= 1:
            result_runtime = _run(
                cmd_prefix + [
                    "dependencyInsight",
                    "--dependency", artifact_id,
                    "--configuration", "runtimeClasspath",
                    "--no-daemon"
                ],
                cwd=workspace,
                verbose=verbose,
                timeout=120
            )

            chain_runtime = _parse_insight_output(result_runtime.stdout, dependency, build_system)
            if chain_runtime and len(chain_runtime) > len(chain):
                chain = chain_runtime
                result = result_runtime
                source = "runtimeClasspath"

        return {
            "success": result.returncode == 0 and len(chain) > 0,
            "parent_chain": chain,
            "source": source,
            "raw_output": result.stdout[:2000] if result.stdout else ""
        }
    else:
        wrapper = os.path.join(workspace, "mvnw")
        _ensure_executable(wrapper)
        cmd_prefix = [wrapper] if os.path.isfile(wrapper) else ["mvn"]
        result = _run(
            cmd_prefix + [
                "dependency:tree",
                f"-Dincludes={group_id}:{artifact_id}",
                "-B"
            ],
            cwd=workspace,
            verbose=verbose,
            timeout=120
        )

        # Parse the output to extract parent chain
        chain = _parse_insight_output(result.stdout, dependency, build_system)

        return {
            "success": result.returncode == 0,
            "parent_chain": chain,
            "source": "compile" if chain else None,
            "raw_output": result.stdout[:2000] if result.stdout else ""
        }


def _parse_insight_output(output: str, target_dep: str, build_system: str) -> list[str]:
    r"""Parse dependencyInsight output to extract the parent chain.

    For Gradle, the output looks like:
    tools.jackson.core:jackson-core:3.1.2
       variant "apiElements" [...]

    tools.jackson.core:jackson-core:3.1.0 -> 3.1.2
    \--- tools.jackson.core:jackson-databind:3.1.0
         \--- org.springframework.boot:spring-boot-jackson:4.0.5
              \--- org.springframework.boot:spring-boot-starter-jackson:4.0.5
                   \--- org.springframework.boot:spring-boot-starter-webflux:4.0.5
                        \--- compileClasspath

    We want to extract the TRANSITIVE chain (the one with arrows showing version override),
    not the direct declaration.
    """
    import re

    chain = []
    lines = output.split("\n")

    if build_system == "gradle":
        # Find lines that show transitive dependency chains
        # We want to capture the chain that has "-> version" which indicates
        # this is a transitive dep being overridden
        in_transitive_chain = False
        current_chain = []

        for line in lines:
            # Look for lines that indicate start of a transitive chain
            # (lines with "-> version" show version override, meaning transitive dep)
            if "->" in line and ":" in line and "---" not in line:
                in_transitive_chain = True
                current_chain = []
                # Extract the dependency from this line
                match = re.search(r'([a-zA-Z0-9._-]+):([a-zA-Z0-9._-]+)', line)
                if match:
                    dep = f"{match.group(1)}:{match.group(2)}"
                    current_chain.append(dep)
                continue

            if in_transitive_chain:
                if "---" in line and ":" in line:
                    match = re.search(r'([a-zA-Z0-9._-]+):([a-zA-Z0-9._-]+)', line)
                    if match:
                        dep = f"{match.group(1)}:{match.group(2)}"
                        if dep not in current_chain:
                            current_chain.append(dep)
                elif "compileClasspath" in line:
                    # End of this chain
                    if current_chain:
                        chain = current_chain
                    break
                elif line.strip() == "" or "variant" in line.lower():
                    # Empty line or variant info might indicate end of chain
                    if current_chain and len(current_chain) > 1:
                        chain = current_chain
                    in_transitive_chain = False
                    current_chain = []

        # If we didn't find a transitive chain with "->", fall back to any chain found
        if not chain:
            for line in lines:
                if "---" in line and ":" in line:
                    match = re.search(r'([a-zA-Z0-9._-]+):([a-zA-Z0-9._-]+)', line)
                    if match:
                        dep = f"{match.group(1)}:{match.group(2)}"
                        if dep not in chain:
                            chain.append(dep)
                elif "compileClasspath" in line and chain:
                    break
    else:
        # Maven format
        for line in lines:
            if ("---" in line or "+-" in line or "\\-" in line) and ":" in line:
                match = re.search(r'([a-zA-Z0-9._-]+):([a-zA-Z0-9._-]+)', line)
                if match:
                    dep = f"{match.group(1)}:{match.group(2)}"
                    if dep not in chain:
                        chain.append(dep)

    # Reverse to get root-to-target order (parent first, target last)
    chain.reverse()
    return chain


def _parse_build_environment_output(output: str, target_dep: str) -> list[str]:
    r"""Parse buildEnvironment output to find a dependency's parent chain.

    buildEnvironment output shows the classpath configuration for the build script itself.
    Format looks like:
    classpath
    +--- org.springframework.boot:spring-boot-gradle-plugin:4.0.5
    |    +--- org.springframework.boot:spring-boot-buildpack-platform:4.0.5
    |    |    +--- tools.jackson.core:jackson-databind:3.1.0
    |    |    |    +--- tools.jackson.core:jackson-core:3.1.0
    ...

    We need to find the target dependency and trace back to its root parent.
    """
    import re

    if not target_dep or ":" not in target_dep:
        return []

    target_group, target_artifact = target_dep.split(":")[:2]
    lines = output.split("\n")

    # Build a tree structure to trace parents
    # Each line has indentation that indicates depth
    chain = []
    found_target = False
    target_depth = -1

    for i, line in enumerate(lines):
        if not line.strip():
            continue

        # Check if this line contains our target dependency
        if target_artifact in line and target_group in line:
            # Found the target, now we need to trace back to find parents
            match = re.search(r'([a-zA-Z0-9._-]+):([a-zA-Z0-9._-]+):([a-zA-Z0-9._-]+)', line)
            if match:
                dep = f"{match.group(1)}:{match.group(2)}"
                if dep == target_dep or match.group(2) == target_artifact:
                    found_target = True
                    # Count leading characters to determine depth
                    target_depth = len(line) - len(line.lstrip())
                    chain = [dep]

                    # Now trace back up to find parents
                    for j in range(i - 1, -1, -1):
                        prev_line = lines[j]
                        if not prev_line.strip():
                            continue

                        prev_depth = len(prev_line) - len(prev_line.lstrip())

                        # If this line has less indentation, it's a parent
                        if prev_depth < target_depth:
                            prev_match = re.search(r'([a-zA-Z0-9._-]+):([a-zA-Z0-9._-]+)', prev_line)
                            if prev_match:
                                parent_dep = f"{prev_match.group(1)}:{prev_match.group(2)}"
                                chain.insert(0, parent_dep)
                                target_depth = prev_depth

                        # If we've reached the root (classpath line or minimal indent)
                        if prev_depth <= 0 or "classpath" in prev_line.lower():
                            break

                    break

    return chain


def _extract_direct_deps(build_content: str, build_system: str) -> set[str]:
    """Extract direct dependencies from build file."""
    import re

    deps = set()

    if build_system == "gradle":
        patterns = [
            r"(?:implementation|api|compileOnly|runtimeOnly|testImplementation|developmentOnly)\s+['\"]([^:]+):([^:'\"]+)",
            r"(?:implementation|api|compileOnly|runtimeOnly|testImplementation|developmentOnly)\s*\(\s*['\"]([^:]+):([^:'\"]+)",
            r"platform\s*\(\s*['\"]([^:]+):([^:'\"]+)",
        ]
        for pattern in patterns:
            for match in re.finditer(pattern, build_content):
                deps.add(f"{match.group(1)}:{match.group(2)}")
    else:
        # Maven
        pattern = r"<groupId>([^<]+)</groupId>\s*<artifactId>([^<]+)</artifactId>"
        for match in re.finditer(pattern, build_content, re.DOTALL):
            deps.add(f"{match.group(1).strip()}:{match.group(2).strip()}")

    return deps


def _find_parent_heuristic(vuln_dep: str, direct_deps: set[str]) -> str | None:
    """Try to find a parent using naming heuristics."""
    artifact = vuln_dep.split(":")[-1] if ":" in vuln_dep else vuln_dep
    base_name = artifact.replace("-spring6", "").replace("-spring5", "").replace("-jakarta", "")

    for dep in direct_deps:
        dep_artifact = dep.split(":")[-1] if ":" in dep else dep
        if base_name.lower() in dep_artifact.lower():
            return dep

    return None


def compile_only(workspace: str, build_system: str, verbose: bool = True) -> dict[str, object]:
    """Run only compilation (no tests) to quickly validate changes.

    Args:
        workspace: Path to the project workspace.
        build_system: The build system type ('gradle' or 'maven').
        verbose: If True, stream output to stderr in real-time.

    Returns:
        Dict with success status and output.
    """
    if build_system == "gradle":
        wrapper = os.path.join(workspace, "gradlew")
        _ensure_executable(wrapper)
        cmd_prefix = [wrapper] if os.path.isfile(wrapper) else ["gradle"]
        result = _run(
            cmd_prefix + ["compileJava", "--no-daemon", "--stacktrace"],
            cwd=workspace,
            verbose=verbose
        )
    else:
        wrapper = os.path.join(workspace, "mvnw")
        _ensure_executable(wrapper)
        cmd_prefix = [wrapper] if os.path.isfile(wrapper) else ["mvn"]
        result = _run(
            cmd_prefix + ["compile", "-B", "-e"],
            cwd=workspace,
            verbose=verbose
        )

    return {
        "success": result.returncode == 0,
        "return_code": result.returncode,
        "stdout": _truncate(result.stdout, 8000),
        "stderr": _truncate(result.stderr, 8000),
    }




