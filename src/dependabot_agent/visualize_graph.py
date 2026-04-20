#!/usr/bin/env python
"""Visualize the LangGraph state machine.

Usage:
    python -m dependabot_agent.visualize_graph

This will generate:
  - graph.png (PNG image)
  - graph.mmd (Mermaid diagram source)
"""

from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def main() -> None:
    from dependabot_agent.graph import graph

    output_dir = Path(__file__).parent.parent.parent.parent  # Project root

    # Method 1: Generate PNG image (requires graphviz/pygraphviz or grandalf)
    try:
        png_bytes = graph.get_graph().draw_png()
        png_path = output_dir / "graph.png"
        png_path.write_bytes(png_bytes)
        print(f"✅ PNG diagram saved to: {png_path}")
    except Exception as e:
        print(f"⚠️  Could not generate PNG (install pygraphviz or grandalf): {e}")

    # Method 2: Generate Mermaid diagram (text-based, works everywhere)
    try:
        mermaid_str = graph.get_graph().draw_mermaid()
        mmd_path = output_dir / "graph.mmd"
        mmd_path.write_text(mermaid_str)
        print(f"✅ Mermaid diagram saved to: {mmd_path}")
        print("\n📊 Mermaid Diagram (paste into https://mermaid.live):\n")
        print(mermaid_str)
    except Exception as e:
        print(f"⚠️  Could not generate Mermaid diagram: {e}")

    # Method 3: Print ASCII representation
    try:
        print("\n📋 Graph Structure:")
        print("-" * 40)
        g = graph.get_graph()
        print(f"Nodes: {list(g.nodes.keys())}")
        print(f"Edges:")
        for edge in g.edges:
            print(f"  {edge}")
    except Exception as e:
        print(f"⚠️  Could not print graph structure: {e}")


if __name__ == "__main__":
    main()

