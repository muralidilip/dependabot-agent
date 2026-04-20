"""Simple agent template package.

This package provides a LangGraph agent that resolves Dependabot
vulnerability warnings by upgrading dependencies and applying exclusions.
"""

from dependabot_agent.graph import build_graph, graph
from dependabot_agent.state import AgentState

__all__ = ["AgentState", "build_graph", "graph"]
