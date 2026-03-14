"""
Complexity Router — classifies a user query before the pipeline runs.
Routes to the right agent subset to avoid wasting compute on simple questions.

Tiers:
  simple  → 2 agents  (visionary + summarizer)
  medium  → 5 agents  (visionary + researcher + mediator + architect + summarizer)
  complex → 10 agents (full pipeline)
"""

import logging

logger = logging.getLogger(__name__)

# ── Routing table ─────────────────────────────────────────────────────────────

AGENT_SUBSETS = {
    "simple": [
        "visionary",
        "summarizer",
    ],
    "medium": [
        "visionary",
        "researcher",
        "mediator",
        "architect",
        "summarizer",
    ],
    "complex": None,  # None = full pipeline (all 9 agents)
}

# ── Router system prompt ───────────────────────────────────────────────────────

_ROUTER_SYSTEM = """\
You are a query complexity classifier for a multi-agent AI debate system.

Classify the incoming query into EXACTLY ONE tier:

SIMPLE
- Factual lookups, definitions, basic how-tos
- One clear correct answer exists
- No trade-offs to weigh
- Examples: "What is blockchain?", "How do I create a Python venv?"

MEDIUM
- Requires analysis, comparison, or a recommendation
- Scope is limited and well-defined
- Some trade-offs exist but the decision space is small
- Examples: "PostgreSQL vs MongoDB for a small SaaS?", "Best way to structure a 5-person dev team?"

COMPLEX
- Strategic, open-ended, high-stakes, or multi-stakeholder
- Multiple valid approaches with significant trade-offs
- Novel problems where the right framing itself is uncertain
- Examples: "Should we pivot our product?", "Design an AI decision-making system", "How do we enter a new market?"

Respond with ONLY one word — simple, medium, or complex. No punctuation. No explanation.\
"""


# ── Classifier ────────────────────────────────────────────────────────────────

def classify(user_idea: str, config: dict) -> str:
    """
    Classify the query complexity using a fast deepseek-chat call.
    Returns one of: 'simple', 'medium', 'complex'.
    Falls back to 'complex' on any error.
    """
    try:
        from api_client import APIClient
        client = APIClient(config)
        result = client._call_deepseek(
            model="deepseek-chat",
            system_prompt=_ROUTER_SYSTEM,
            user_prompt=f"Query: {user_idea}",
            max_tokens=5,
        )
        tier = result["content"].strip().lower().split()[0].rstrip(".,;:")
        if tier in AGENT_SUBSETS:
            logger.info(f"[Router] Classified as: {tier.upper()}")
            return tier
        logger.warning(f"[Router] Unexpected response '{tier}', defaulting to complex")
        return "complex"
    except Exception as exc:
        logger.warning(f"[Router] Classification failed ({exc}), defaulting to complex")
        return "complex"


def get_active_agents(complexity: str) -> list[str] | None:
    """Return the agent list for a given complexity tier (None = all agents)."""
    return AGENT_SUBSETS.get(complexity)
