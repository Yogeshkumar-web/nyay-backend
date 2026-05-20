"""
Unified prompt loader for all VakilSuite agents.
Prompts live in: backend/prompts/agents/{agent_name}/{prompt_name}.txt
Cached on first load — no repeated disk reads.
"""
import logging
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

# backend/prompts/ directory — resolved relative to this file's location
_PROMPTS_ROOT = Path(__file__).parent.parent.parent.parent / "prompts"


@lru_cache(maxsize=64)
def load_agent_prompt(agent: str, name: str) -> str:
    """
    Load prompts/agents/{agent}/{name}.txt and return its contents.
    Results are cached — safe to call on every node invocation.

    Raises FileNotFoundError with a clear message if the file is missing,
    so missing prompts fail loudly at runtime rather than silently.
    """
    path = _PROMPTS_ROOT / "agents" / agent / f"{name}.txt"
    if not path.exists():
        raise FileNotFoundError(
            f"Agent prompt not found: {path}\n"
            f"Expected: prompts/agents/{agent}/{name}.txt"
        )
    content = path.read_text(encoding="utf-8")
    logger.debug(
        "Loaded agent prompt: agents/%s/%s.txt (%d chars)", agent, name, len(content)
    )
    return content
