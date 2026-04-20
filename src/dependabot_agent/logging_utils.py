"""Logging utilities for the Dependabot agent nodes.

Provides consistent logging across all graph nodes with clear visual indicators.
"""

from __future__ import annotations

import sys
from typing import Any


def log_node_start(node_name: str, description: str = "") -> None:
    """Log the start of a node execution."""
    print(f"\n{'─' * 60}", file=sys.stderr)
    print(f"📌 Node: {node_name}", file=sys.stderr)
    if description:
        print(f"   {description}", file=sys.stderr)
    print(f"{'─' * 60}", file=sys.stderr)


def log_node_success(message: str) -> None:
    """Log a successful node completion."""
    print(f"   ✅ {message}", file=sys.stderr)


def log_node_error(message: str, error: Exception | str | None = None) -> None:
    """Log a node error."""
    print(f"   ❌ {message}", file=sys.stderr)
    if error:
        print(f"   ⚠️  Error: {error}", file=sys.stderr)


def log_node_warning(message: str) -> None:
    """Log a warning (non-fatal issue)."""
    print(f"   ⚠️  {message}", file=sys.stderr)


def log_node_info(message: str) -> None:
    """Log informational message."""
    print(f"   ℹ️  {message}", file=sys.stderr)


def log_node_progress(message: str) -> None:
    """Log progress within a node."""
    print(f"   → {message}", file=sys.stderr)


def log_alerts_summary(alerts: list[dict[str, Any]]) -> None:
    """Log a summary of Dependabot alerts."""
    count = len(alerts)
    print(f"   📊 Found {count} Dependabot alert(s)", file=sys.stderr)

    if count > 0:
        # Group by severity
        severities: dict[str, int] = {}
        for alert in alerts:
            sev = alert.get("security_advisory", {}).get("severity", "unknown")
            severities[sev] = severities.get(sev, 0) + 1

        for sev, cnt in sorted(severities.items(), key=lambda x: {"critical": 0, "high": 1, "medium": 2, "low": 3}.get(x[0], 4)):
            icon = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}.get(sev, "⚪")
            print(f"      {icon} {sev}: {cnt}", file=sys.stderr)


def log_upgrades_summary(upgrades: list[dict[str, Any]], title: str = "Planned upgrades") -> None:
    """Log a summary of planned or applied upgrades."""
    count = len(upgrades)
    print(f"   📋 {title}: {count}", file=sys.stderr)

    for u in upgrades[:5]:  # Show first 5
        group = u.get("group_id", "?")
        artifact = u.get("artifact_id", "")
        current = u.get("current_version", "?")
        target = u.get("target_version", "?")
        coord = f"{group}:{artifact}" if artifact else group
        print(f"      • {coord}: {current} → {target}", file=sys.stderr)

    if count > 5:
        print(f"      ... and {count - 5} more", file=sys.stderr)


def log_build_result(success: bool, context: str = "Build") -> None:
    """Log build result with clear visual indicator."""
    if success:
        print(f"   ✅ {context} succeeded", file=sys.stderr)
    else:
        print(f"   ❌ {context} failed", file=sys.stderr)


def log_build_error_summary(output: str, max_lines: int = 10) -> None:
    """Log a summary of build errors for quick diagnosis."""
    if not output:
        return

    # Look for common error patterns
    error_lines = []
    for line in output.split("\n"):
        line_lower = line.lower()
        if any(kw in line_lower for kw in ["error:", "failure:", "failed", "exception", "could not resolve"]):
            error_lines.append(line.strip())

    if error_lines:
        print(f"   📝 Error summary:", file=sys.stderr)
        for line in error_lines[:max_lines]:
            if line:
                # Truncate long lines
                if len(line) > 120:
                    line = line[:117] + "..."
                print(f"      {line}", file=sys.stderr)
        if len(error_lines) > max_lines:
            print(f"      ... and {len(error_lines) - max_lines} more error lines", file=sys.stderr)


def log_workspace_info(workspace: str, build_system: str, build_file: str) -> None:
    """Log workspace setup information."""
    print(f"   📁 Workspace: {workspace}", file=sys.stderr)
    print(f"   🔧 Build system: {build_system}", file=sys.stderr)
    print(f"   📄 Build file: {build_file}", file=sys.stderr)


def log_pr_created(pr_url: str) -> None:
    """Log PR creation success."""
    print(f"   🔗 Pull Request created: {pr_url}", file=sys.stderr)


def log_binary_search_state(
    pending: int,
    good: int,
    bad: int,
    deferred: int = 0
) -> None:
    """Log the current state of binary search."""
    print(f"   🔍 Binary search state:", file=sys.stderr)
    print(f"      • Pending: {pending}", file=sys.stderr)
    print(f"      • Good: {good}", file=sys.stderr)
    print(f"      • Bad: {bad}", file=sys.stderr)
    if deferred > 0:
        print(f"      • Deferred: {deferred}", file=sys.stderr)

