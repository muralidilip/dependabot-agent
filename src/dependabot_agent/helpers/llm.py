"""LLM initialization and content extraction helpers."""

from __future__ import annotations

import os

from dotenv import load_dotenv

load_dotenv()

# ── LLM setup ────────────────────────────────────────────────────────────
DEFAULT_MODEL = os.getenv("SIMPLE_AGENT_MODEL")
# Temperature 0 for deterministic, reproducible behavior
LLM_TEMPERATURE = 0


def get_llm():
    """Lazy-init the chat model so env vars are loaded first.

    Uses temperature=0 for deterministic behavior across invocations.
    """
    from langchain.chat_models import init_chat_model

    model = DEFAULT_MODEL
    if ":" not in model and os.getenv("GOOGLE_API_KEY"):
        model = f"google_genai:{model}"
    return init_chat_model(model, temperature=LLM_TEMPERATURE)


def extract_build_content(response_content: str) -> str:
    """Extract build file content from LLM response."""
    content = response_content.strip()
    if "```gradle" in content:
        content = content.split("```gradle")[1].split("```")[0].strip()
    elif "```groovy" in content:
        content = content.split("```groovy")[1].split("```")[0].strip()
    elif "```xml" in content:
        content = content.split("```xml")[1].split("```")[0].strip()
    elif "```" in content:
        parts = content.split("```")
        if len(parts) >= 2:
            content = parts[1].strip()
    return content

