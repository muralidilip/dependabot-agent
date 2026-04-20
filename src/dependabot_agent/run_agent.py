#!/usr/bin/env python
"""CLI runner for the Dependabot vulnerability resolution agent.

Usage:
    python -m dependabot_agent.run_agent muralidilip/dependabot-test
"""

from __future__ import annotations

import sys

from dotenv import load_dotenv

load_dotenv()


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python -m dependabot_agent.run_agent <owner/repo>")
        sys.exit(1)

    repo = sys.argv[1]
    print(f"🚀 Starting Dependabot vulnerability resolution for: {repo}")
    print("=" * 60)

    from dependabot_agent.graph import graph

    initial_state = {
        "messages": [],
        "repo": repo,
        "workspace": "",
        "build_system": "",
        "build_file": "",
        "original_build_content": "",
        "current_build_content": "",
        "alerts": [],
        "planned_upgrades": [],
        "build_success": False,
        "build_output": "",
        "upgrade_attempt_count": 0,
        "exclusion_attempt_count": 0,
        "error": "",
        "pr_url": "",
    }

    # Stream events - logging is now handled within each node
    for event in graph.stream(initial_state, stream_mode="updates"):
        # Just let the nodes handle their own logging
        pass

    print(f"\n{'=' * 60}")
    print("✅ Agent workflow complete!")


if __name__ == "__main__":
    main()

