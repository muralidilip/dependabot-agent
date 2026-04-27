"""Try exclusions node."""

from __future__ import annotations

import json

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from dependabot_agent.helpers import (
    apply_exclusions_to_content,
    get_llm,
    validate_no_dependencies_removed,
)
from dependabot_agent.logging_utils import (
    log_node_error,
    log_node_info,
    log_node_progress,
    log_node_start,
    log_node_success,
    log_node_warning,
)
from dependabot_agent.state import AgentState
from dependabot_agent.tools.agent_tools import write_build_file


EXCLUSIONS_PROMPT = """\
You are an expert Java/Gradle/Maven dependency resolution agent.

Given the remaining vulnerabilities and their parent dependencies, output a JSON plan for exclusions and version pins.

## Remaining Vulnerabilities:
{vulnerabilities}

## Current Build File:
```
{build_content}
```

Output a JSON object with this EXACT structure:
```json
{{
  "exclusions": [
    {{
      "parent_group": "org.springframework.boot",
      "parent_artifact": "spring-boot-starter-thymeleaf",
      "excludes": [
        {{"group": "org.thymeleaf", "artifact": "thymeleaf"}}
      ]
    }}
  ],
  "pins": [
    {{"group": "org.thymeleaf", "artifact": "thymeleaf", "version": "3.1.4.RELEASE"}}
  ],
  "skipped": [
    {{"group": "some.group", "artifact": "some-artifact", "reason": "No safe version"}}
  ]
}}
```

RULES:
- For transitive vulnerabilities: add exclusion from parent AND pin to first_patched_version
- For direct vulnerabilities (parents contains "DIRECT"): just pin to first_patched_version, no exclusion
- Use the first_patched_version provided in each vulnerability
- If no first_patched_version, add to skipped list
- Output ONLY valid JSON, nothing else
"""


def try_exclusions_node(state: AgentState) -> dict:
    """Use LLM to plan and apply exclusions for remaining vulnerabilities."""
    log_node_start("try_exclusions", "Planning and applying exclusions")

    attempt = state.get("exclusion_attempt_count", 0) + 1
    max_retries = state.get("max_exclusion_retries", 3)
    log_node_info(f"Exclusion attempt {attempt}/{max_retries}")

    remaining_vulns = state.get("remaining_vulnerabilities", [])
    build_content = state["current_build_content"]
    build_system = state["build_system"]

    if not remaining_vulns:
        log_node_info("No remaining vulnerabilities - nothing to exclude")
        return {
            "exclusion_attempt_count": attempt,
            "has_changes": False,
            "messages": [AIMessage(content="No exclusions needed")],
        }

    log_node_info(f"Processing {len(remaining_vulns)} remaining vulnerabilities")
    for vuln in remaining_vulns:
        pkg = vuln.get("package", "")
        log_node_progress(f"  - {pkg}@{vuln.get('current_version', '?')} → needs {vuln.get('first_patched_version', 'latest')}")

    # Invoke LLM to plan exclusions
    log_node_progress("Invoking LLM to plan exclusions...")
    llm = get_llm()

    vuln_summary = json.dumps(remaining_vulns, indent=2)
    prompt = EXCLUSIONS_PROMPT.format(
        vulnerabilities=vuln_summary,
        build_content=build_content
    )

    response = llm.invoke([
        SystemMessage(content="You are a dependency resolution expert. Output only valid JSON."),
        HumanMessage(content=prompt),
    ])

    # Parse LLM response
    try:
        content = response.content.strip()
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]

        plan = json.loads(content.strip())
        exclusions = plan.get("exclusions", [])
        pins = plan.get("pins", [])
        skipped = plan.get("skipped", [])
    except (json.JSONDecodeError, IndexError, KeyError) as e:
        log_node_warning(f"Failed to parse LLM response: {e}")
        return {
            "exclusion_attempt_count": attempt,
            "messages": [AIMessage(content=f"Failed to parse exclusion plan: {e}")],
            "error": str(e),
        }

    log_node_info(f"LLM planned: {len(exclusions)} exclusions, {len(pins)} pins, {len(skipped)} skipped")

    if exclusions:
        log_node_info("Exclusions:")
        for excl in exclusions:
            parent = f"{excl.get('parent_group', '')}:{excl.get('parent_artifact', '')}"
            excludes = [f"{e['group']}:{e['artifact']}" for e in excl.get('excludes', [])]
            log_node_progress(f"  {parent} → exclude {', '.join(excludes)}")

    if pins:
        log_node_info("Pins:")
        for pin in pins:
            log_node_progress(f"  {pin['group']}:{pin['artifact']}:{pin['version']}")

    # Apply changes
    log_node_progress("Applying changes to build file...")

    try:
        new_content = apply_exclusions_to_content(
            build_content, exclusions, pins, skipped, build_system
        )

        is_valid, removed_deps = validate_no_dependencies_removed(
            build_content, new_content, build_system
        )

        if not is_valid:
            log_node_error(f"Application removed dependencies: {removed_deps}")
            new_content = build_content

        write_build_file.invoke({
            "workspace": state["workspace"],
            "content": new_content,
        })

        content_changed = new_content.strip() != state["original_build_content"].strip()

        if content_changed:
            log_node_success(f"Applied {len(exclusions)} exclusions, {len(pins)} pins")
        else:
            log_node_warning("No changes were made to the build file")

        return {
            "current_build_content": new_content,
            "exclusion_attempt_count": attempt,
            "has_changes": content_changed,
            "messages": [AIMessage(content=f"Applied {len(exclusions)} exclusions, {len(pins)} pins")],
        }
    except Exception as e:
        log_node_error("Failed to apply exclusions", e)
        return {
            "exclusion_attempt_count": attempt,
            "messages": [AIMessage(content=f"Failed to apply exclusions: {e}")],
            "error": str(e),
        }
