from langgraph.pregel import Pregel

from dependabot_agent.graph import graph
from dependabot_agent.github_dependabot import fetch_dependabot_alerts


def test_graph_compiles() -> None:
    assert isinstance(graph, Pregel)


def test_dependabot_tool_exported_from_github_dependabot() -> None:
    assert fetch_dependabot_alerts.name == "fetch_dependabot_alerts"


