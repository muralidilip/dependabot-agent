# Simple Agent Template

Minimal deployment template for a LangChain agent built with `create_agent(...)`.

## What this template gives you

- A deployable LangGraph entrypoint at `src/simple_agent/graph.py`.
- Two small tools (`utc_now`, `calculator`) for predictable local behavior.
- `langgraph.json` configured for LangSmith/LangGraph deployment.
- A `uv`-managed local workflow with a small `Makefile` wrapper and starter tests.

## Quickstart

1. Sync the project with `uv`:

```bash
uv sync --dev
```

2. Configure environment:

```bash
cp .env.example .env
```

For Claude models, make sure `.env` contains `ANTHROPIC_API_KEY`. If you want the agent to query GitHub Dependabot alerts, also set `GITHUB_TOKEN` and optionally `GITHUB_DEFAULT_OWNER`.

3. Run locally:

```bash
uv run langgraph dev
```

Optional `make` wrappers:

```bash
make dev
make run
```

## Fetch GitHub Dependabot alerts

The agent includes a `fetch_dependabot_alerts` tool that uses the GitHub REST API with `GITHUB_TOKEN`.

- Pass `repo` as `owner/repo`, or
- Pass only `repo` and set `GITHUB_DEFAULT_OWNER`.

Example direct invocation:

```bash
uv run python - <<'PY'
from simple_agent.github_dependabot import fetch_dependabot_alerts

result = fetch_dependabot_alerts.invoke({
	"repo": "owner/repository",
	"state": "open",
	"per_page": 30,
	"page": 1,
})

print(result)
PY
```

Example prompt while running `uv run langgraph dev`:

```text
Use fetch_dependabot_alerts for owner/repository and summarize the open Dependabot alerts by severity.
```

## Tests and lint

```bash
make test
make integration-tests
make lint
make format
```

Integration tests are skipped unless `ANTHROPIC_API_KEY` is set.

## Deploy to LangSmith

1. Push this template to a Git repository.
2. In LangSmith, create a new Deployment from that repo.
3. Set required environment variables (`ANTHROPIC_API_KEY`, optionally `LANGSMITH_API_KEY`).
4. Deploy using `langgraph.json` defaults.

## Reference docs

- LangChain quickstart: https://docs.langchain.com/oss/python/langchain/quickstart
- LangChain deployment: https://docs.langchain.com/oss/python/langchain/deploy
