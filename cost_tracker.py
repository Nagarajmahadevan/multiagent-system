"""
Cost tracker that logs token usage and calculates estimated cost in INR
for each agent call. Reads pricing from config.yaml.
"""

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class AgentCostRecord:
    """Cost record for a single agent run."""

    agent_name: str
    model: str
    input_tokens: int
    output_tokens: int
    input_cost_inr: float
    output_cost_inr: float

    @property
    def total_cost_inr(self) -> float:
        return self.input_cost_inr + self.output_cost_inr


class CostTracker:
    """Tracks token usage and cost across all agent calls."""

    def __init__(self, pricing_config: dict):
        """
        Args:
            pricing_config: The 'pricing' section from config.yaml.
                            Maps model name -> {input_per_million, output_per_million}
        """
        self.pricing = pricing_config
        self.records: list[AgentCostRecord] = []

    def record(
        self, agent_name: str, model: str, input_tokens: int, output_tokens: int
    ) -> AgentCostRecord:
        """
        Record token usage for an agent and calculate cost.

        Returns:
            The AgentCostRecord created.
        """
        model_pricing = self.pricing.get(model)
        if not model_pricing:
            logger.warning(
                f"No pricing found for model '{model}'. Costs will be zero."
            )
            input_cost = 0.0
            output_cost = 0.0
        else:
            input_cost = (input_tokens / 1_000_000) * model_pricing["input_per_million"]
            output_cost = (
                (output_tokens / 1_000_000) * model_pricing["output_per_million"]
            )

        record = AgentCostRecord(
            agent_name=agent_name,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            input_cost_inr=round(input_cost, 6),
            output_cost_inr=round(output_cost, 6),
        )
        self.records.append(record)

        logger.info(
            f"[Cost] {agent_name}: {input_tokens} in / {output_tokens} out | "
            f"INR {record.total_cost_inr:.6f}"
        )
        return record

    @property
    def total_input_tokens(self) -> int:
        return sum(r.input_tokens for r in self.records)

    @property
    def total_output_tokens(self) -> int:
        return sum(r.output_tokens for r in self.records)

    @property
    def total_cost_inr(self) -> float:
        return sum(r.total_cost_inr for r in self.records)

    def get_summary_table(self) -> str:
        """Return a formatted markdown table of per-agent costs."""
        lines = [
            "| Agent | Model | Input Tokens | Output Tokens | Input Cost (INR) | Output Cost (INR) | Total (INR) |",
            "|-------|-------|-------------|--------------|-----------------|-------------------|-------------|",
        ]
        for r in self.records:
            lines.append(
                f"| {r.agent_name} | {r.model} | {r.input_tokens:,} | "
                f"{r.output_tokens:,} | {r.input_cost_inr:.6f} | "
                f"{r.output_cost_inr:.6f} | {r.total_cost_inr:.6f} |"
            )
        lines.append(
            f"| **TOTAL** | — | **{self.total_input_tokens:,}** | "
            f"**{self.total_output_tokens:,}** | — | — | "
            f"**{self.total_cost_inr:.6f}** |"
        )
        return "\n".join(lines)
