"""Try exclusions node."""

from __future__ import annotations

from langchain_core.messages import AIMessage

from dependabot_agent.helpers import apply_exclusions_to_content, validate_no_dependencies_removed
from dependabot_agent.logging_utils import (
    log_node_error,
    log_node_info,
    log_node_progress,
    log_node_start,
    log_node_success,
    log_node_warning,
)
from dependabot_agent.state import AgentState
from dependabot_agent.tools.agent_tools import (
    build_vulnerability_map,
    lookup_absolute_latest_maven_version,
    write_build_file,
)


def try_exclusions_node(state: AgentState) -> dict:
    """Plan and apply exclusions using targeted dependency lookups.

    This approach:
    1. Gets vulnerable packages from alerts
    2. Uses targeted `dependencyInsight` queries to find parent for each
    3. Builds exclusions only for deps found in tree
    4. Just pins version for deps not found (no exclusion needed)
    """
    log_node_start("try_exclusions", "Planning and applying exclusions")

    attempt = state.get("exclusion_attempt_count", 0) + 1
    max_retries = state.get("max_exclusion_retries", 3)
    log_node_info(f"Exclusion attempt {attempt}/{max_retries}")

    # Get unique vulnerable packages from alerts
    vulnerable_packages = []
    seen = set()
    for alert in state["alerts"]:
        pkg = alert.get("package", "")
        if pkg and pkg not in seen:
            vulnerable_packages.append(pkg)
            seen.add(pkg)

    log_node_info(f"Found {len(vulnerable_packages)} unique vulnerable packages")
    for pkg in vulnerable_packages:
        log_node_progress(f"  - {pkg}")

    # Build vulnerability map using targeted queries
    log_node_progress("Building vulnerability map with targeted dependency lookups...")
    try:
        vuln_map_result = build_vulnerability_map.invoke({
            "workspace": state["workspace"],
            "vulnerable_packages": vulnerable_packages,
            "build_content": state["current_build_content"],
            "build_system": state["build_system"],
            "context": "try_exclusions"
        })
    except Exception as e:
        log_node_error(f"Failed to build vulnerability map: {e}")
        return {
            "exclusion_attempt_count": attempt,
            "messages": [AIMessage(content=f"Failed to analyze dependencies: {e}")],
            "error": str(e),
        }

    parent_to_vulns = vuln_map_result.get("parent_to_vulns", {})
    buildscript_vulns = vuln_map_result.get("buildscript_vulns", [])
    direct_upgrades = vuln_map_result.get("direct_upgrades", [])
    not_found = vuln_map_result.get("not_found", [])

    # Log results
    log_node_info(f"Vulnerability map results:")
    log_node_info(f"  - Parents with vulnerabilities: {len(parent_to_vulns)}")
    for parent, vulns in parent_to_vulns.items():
        log_node_progress(f"    {parent}:")
        for v in vulns:
            inferred = " (inferred)" if v.get("inferred") else ""
            source = f" [{v.get('source', '')}]" if v.get("source") else ""
            log_node_progress(f"      → {v['dep']}{inferred}{source}")

    log_node_info(f"  - Buildscript/plugin deps: {len(buildscript_vulns)}")
    for dep in buildscript_vulns:
        log_node_progress(f"    → {dep} [buildEnvironment]")

    log_node_info(f"  - Direct deps to upgrade: {len(direct_upgrades)}")
    for dep in direct_upgrades:
        log_node_progress(f"    → {dep}")

    log_node_info(f"  - Not found (will just pin): {len(not_found)}")
    for dep in not_found:
        log_node_progress(f"    → {dep}")

    # Build exclusions list from parent_to_vulns
    exclusions = []
    for parent, vulns in parent_to_vulns.items():
        parent_group, parent_artifact = parent.split(":") if ":" in parent else ("", parent)
        excludes = []
        for v in vulns:
            vdep = v.get("dep", "")
            if ":" in vdep:
                g, a = vdep.split(":")
                excludes.append({"group": g, "artifact": a})

        if excludes:
            exclusions.append({
                "parent_group": parent_group,
                "parent_artifact": parent_artifact,
                "excludes": excludes
            })

    log_node_info(f"Built {len(exclusions)} exclusion rules")

    # Build buildscript exclusions for plugin dependencies
    buildscript_exclusions = []
    for dep in buildscript_vulns:
        if ":" in dep:
            g, a = dep.split(":")
            buildscript_exclusions.append({"group": g, "artifact": a})

    if buildscript_exclusions:
        log_node_info(f"Built {len(buildscript_exclusions)} buildscript exclusion rules")

    # Look up safe versions for ALL vulnerable deps
    log_node_progress("Looking up safe versions...")

    pins = []
    buildscript_pins = []  # Pins for buildscript dependencies
    all_vulns_to_pin = set()

    # Add deps that need exclusions
    for excl in exclusions:
        for ex in excl.get("excludes", []):
            all_vulns_to_pin.add(f"{ex['group']}:{ex['artifact']}")

    # Add direct deps
    for dep in direct_upgrades:
        all_vulns_to_pin.add(dep)

    # Add not_found deps (just pin, no exclusion)
    for dep in not_found:
        all_vulns_to_pin.add(dep)

    for vuln in all_vulns_to_pin:
        if ":" not in vuln:
            continue
        group, artifact = vuln.split(":")
        try:
            # Use absolute latest version lookup since we don't know the current
            # version of excluded transitive dependencies
            result = lookup_absolute_latest_maven_version.invoke({
                "group_id": group,
                "artifact_id": artifact,
            })
            version = result.get("latest_version", "")
            found = result.get("found", False)
            if found and version:
                pins.append({
                    "group": group,
                    "artifact": artifact,
                    "version": version
                })
                log_node_progress(f"  {group}:{artifact} -> {version}")
            else:
                log_node_warning(f"  No version found for {group}:{artifact}")
        except Exception as e:
            log_node_warning(f"  Version lookup failed for {group}:{artifact}: {e}")

    # Look up versions for buildscript dependencies
    for dep in buildscript_vulns:
        if ":" not in dep:
            continue
        group, artifact = dep.split(":")
        try:
            result = lookup_absolute_latest_maven_version.invoke({
                "group_id": group,
                "artifact_id": artifact,
            })
            version = result.get("latest_version", "")
            found = result.get("found", False)
            if found and version:
                buildscript_pins.append({
                    "group": group,
                    "artifact": artifact,
                    "version": version
                })
                log_node_progress(f"  {group}:{artifact} -> {version} [buildscript]")
            else:
                log_node_warning(f"  No version found for {group}:{artifact} [buildscript]")
        except Exception as e:
            log_node_warning(f"  Version lookup failed for {group}:{artifact}: {e}")

    # Build skipped list (only for deps we couldn't find versions for)
    skipped = []
    pinned_deps = {f"{p['group']}:{p['artifact']}" for p in pins}
    for dep in not_found:
        if dep not in pinned_deps:
            group, artifact = dep.split(":") if ":" in dep else ("", dep)
            skipped.append({
                "group": group,
                "artifact": artifact,
                "reason": "Could not find safe version"
            })

    # Count how many pins are for existing deps (direct upgrades) vs new pins (transitive)
    build_content = state["current_build_content"]
    already_correct = []  # Already at target version
    needs_upgrade = []    # Existing deps that need version bump
    new_pins = []         # New transitive deps to add

    for pin in pins:
        group = pin.get("group", "")
        artifact = pin.get("artifact", "")
        version = pin.get("version", "")
        coord = f"{group}:{artifact}"
        coord_with_version = f"{group}:{artifact}:{version}"

        # Check if exact version already present
        if coord_with_version in build_content:
            already_correct.append(pin)
        # Check if this is an existing direct dependency (needs upgrade)
        elif (f"'{coord}'" in build_content or
              f'"{coord}"' in build_content or
              f"'{coord}:" in build_content or
              f'"{coord}:' in build_content):
            needs_upgrade.append(pin)
        else:
            # New transitive dependency - add as pin
            new_pins.append(pin)

    log_node_info(f"Final: {len(exclusions)} exclusions, {len(buildscript_exclusions)} buildscript exclusions, {len(needs_upgrade)} to upgrade, {len(new_pins)} new pins, {len(already_correct)} already correct, {len(skipped)} skipped")

    if already_correct:
        log_node_info("Already at correct version:")
        for pin in already_correct:
            log_node_progress(f"  ✓ {pin.get('group', '')}:{pin.get('artifact', '')}:{pin.get('version', '')} (no change needed)")

    if exclusions:
        log_node_info("Dependency exclusions:")
        for excl in exclusions:
            parent = f"{excl.get('parent_group', '')}:{excl.get('parent_artifact', '')}"
            excludes = [f"{e.get('group', '')}:{e.get('artifact', '')}" for e in excl.get('excludes', [])]
            log_node_progress(f"  Exclude from {parent}: {', '.join(excludes)}")

    if buildscript_exclusions:
        log_node_info("Buildscript/plugin exclusions (will use force):")
        for excl in buildscript_exclusions:
            log_node_progress(f"  Force: {excl.get('group', '')}:{excl.get('artifact', '')}")

    if needs_upgrade:
        log_node_info("Direct dependencies to upgrade:")
        for pin in needs_upgrade:
            log_node_progress(f"  ↑ Upgrade: {pin.get('group', '')}:{pin.get('artifact', '')} → {pin.get('version', '')}")

    if new_pins:
        log_node_info("New transitive dependencies to pin:")
        for pin in new_pins:
            log_node_progress(f"  + Pin: {pin.get('group', '')}:{pin.get('artifact', '')}:{pin.get('version', '')}")

    if buildscript_pins:
        log_node_info("Buildscript dependencies to force:")
        for pin in buildscript_pins:
            log_node_progress(f"  ⚡ Force: {pin.get('group', '')}:{pin.get('artifact', '')}:{pin.get('version', '')}")

    # Apply changes programmatically - this is the safe part
    log_node_progress("Applying changes to build file...")

    try:
        new_content = apply_exclusions_to_content(
            build_content,
            exclusions,
            pins,
            skipped,
            state["build_system"],
            buildscript_pins=buildscript_pins
        )

        # Validate that no dependencies were removed (sanity check)
        is_valid, removed_deps = validate_no_dependencies_removed(
            build_content,
            new_content,
            state["build_system"]
        )

        if not is_valid:
            log_node_error(f"Programmatic application removed dependencies: {removed_deps}")
            log_node_warning("This shouldn't happen - keeping original content")
            new_content = build_content

        write_build_file.invoke({
            "workspace": state["workspace"],
            "content": new_content,
        })

        content_changed = new_content.strip() != state["original_build_content"].strip()

        if content_changed:
            log_node_success(f"Applied {len(exclusions)} exclusions, {len(needs_upgrade)} upgrades, {len(new_pins)} new pins")
        else:
            if already_correct:
                log_node_info(f"All {len(already_correct)} dependencies already at correct versions - no changes needed")
            else:
                log_node_warning("No changes were made to the build file")

        return {
            "current_build_content": new_content,
            "exclusion_attempt_count": attempt,
            "has_changes": content_changed,
            "messages": [AIMessage(content=f"Applied {len(exclusions)} exclusions, {len(needs_upgrade)} upgrades, {len(new_pins)} new pins")],
        }
    except Exception as e:
        log_node_error("Failed to apply exclusions", e)
        return {
            "messages": [AIMessage(content=f"Failed to apply exclusions: {e}")],
            "error": str(e),
        }

