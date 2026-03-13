"""
Pipeline orchestration — runs all agents in sequential order.
Debate-and-solve pipeline: agents explore, debate, and converge on the best solution.
"""

import time
import logging
from datetime import datetime

from agents import (
    AGENT_ORDER,
    AGENT_DISPLAY_NAMES,
    AGENT_LAYERS,
    SYSTEM_PROMPTS,
    build_user_prompt,
)
from api_client import APIClient
from cost_tracker import CostTracker

logger = logging.getLogger(__name__)


def _print(msg: str = ""):
    """Print to stdout and flush immediately so output appears in real time."""
    print(msg, flush=True)


def _banner(title: str, char: str = "=", width: int = 60):
    """Print a prominent banner line."""
    _print(f"\n{char * width}")
    _print(f"  {title}")
    _print(f"{char * width}")


def _step(msg: str):
    """Print a step indicator."""
    _print(f"  -> {msg}")


def _ok(msg: str):
    """Print a success message."""
    _print(f"  [OK] {msg}")


def _fail(msg: str):
    """Print a failure message."""
    _print(f"  [FAIL] {msg}")


def _warn(msg: str):
    """Print a warning message."""
    _print(f"  [WARN] {msg}")


class Pipeline:
    """Runs the full agent debate pipeline."""

    def __init__(self, config: dict):
        self.config = config
        self.client = APIClient(config)
        self.cost_tracker = CostTracker(config["pricing"])
        self.pipeline_cfg = config["pipeline"]

        self.max_retries = self.pipeline_cfg.get("max_retries", 3)
        self.retry_delay = self.pipeline_cfg.get("retry_delay_seconds", 5)
        self.continue_on_failure = self.pipeline_cfg.get("continue_on_failure", True)

        # Stores the output of each completed agent
        self.outputs: dict[str, str] = {}
        # Stores any errors that occurred
        self.errors: dict[str, str] = {}

    def run(self, user_idea: str) -> dict:
        """
        Execute the full pipeline for a given idea/question.

        Returns:
            dict with keys: outputs, errors, cost_summary, cost_tracker, elapsed_seconds
        """
        _banner("DEBATE PIPELINE STARTED")
        _print(f"  Question: {user_idea[:120]}...")
        _print(f"  Agents: {len(AGENT_ORDER)} in pipeline")
        _print()

        # Show the pipeline flow
        current_layer = None
        for agent_name in AGENT_ORDER:
            layer = AGENT_LAYERS[agent_name]
            if layer != current_layer:
                current_layer = layer
                _print(f"  {layer}")
            _print(f"    - {AGENT_DISPLAY_NAMES[agent_name]}")

        start_time = time.time()
        total_agents = len(AGENT_ORDER)

        for idx, agent_name in enumerate(AGENT_ORDER, 1):
            display = AGENT_DISPLAY_NAMES[agent_name]
            layer = AGENT_LAYERS[agent_name]
            agent_cfg = self.config["agents"][agent_name]
            model = agent_cfg["model"]
            provider = agent_cfg["provider"]
            token_limit = self.config["token_limits"].get(agent_name, 500)

            _print()
            _print(f"{'─' * 60}")
            _print(f"  [{idx}/{total_agents}] {display}")
            _print(f"  Layer:    {layer}")
            _print(f"  Model:    {model} ({provider})")
            _print(f"  Tokens:   max {token_limit} output tokens")
            _print(f"{'─' * 60}")

            # Run the agent
            system_prompt = SYSTEM_PROMPTS[agent_name]
            user_prompt = build_user_prompt(agent_name, user_idea, self.outputs)

            _step(f"Calling {provider} API ({model})...")
            agent_start = time.time()

            result = self._call_with_retries(
                agent_name, system_prompt, user_prompt, token_limit
            )

            agent_elapsed = time.time() - agent_start

            if result is not None:
                self.outputs[agent_name] = result["content"]
                self.cost_tracker.record(
                    agent_name=agent_name,
                    model=result["model"],
                    input_tokens=result["input_tokens"],
                    output_tokens=result["output_tokens"],
                )
                _ok(
                    f"{display} completed in {agent_elapsed:.1f}s "
                    f"({result['input_tokens']} in / {result['output_tokens']} out tokens)"
                )
                # Show a preview of the output
                preview = result["content"][:150].replace("\n", " ").strip()
                _print(f"  Preview: {preview}...")
            else:
                self.outputs[agent_name] = ""
                _fail(f"{display} FAILED after {agent_elapsed:.1f}s")

                if not self.continue_on_failure:
                    _fail("Pipeline halted due to agent failure.")
                    break

        elapsed = time.time() - start_time

        _banner("PIPELINE COMPLETED")
        _print(f"  Total time:  {elapsed:.1f}s")
        _print(f"  Total cost:  INR {self.cost_tracker.total_cost_inr:.6f}")
        _print(f"  Agents run:  {len([v for v in self.outputs.values() if v])}/{total_agents} succeeded")
        if self.errors:
            _print(f"  Errors:      {len(self.errors)} agent(s) had issues")

        return {
            "outputs": self.outputs,
            "errors": self.errors,
            "cost_summary": self.cost_tracker.get_summary_table(),
            "cost_tracker": self.cost_tracker,
            "elapsed_seconds": elapsed,
        }

    # ─────────────────────────────────────────────────────────────────────
    # API call with retries
    # ─────────────────────────────────────────────────────────────────────

    def _call_with_retries(
        self,
        agent_name: str,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int,
    ) -> dict | None:
        """
        Call an agent's API with retry logic.

        Returns:
            The API response dict, or None if all retries failed.
        """
        for attempt in range(1, self.max_retries + 1):
            try:
                result = self.client.call_agent(
                    agent_name=agent_name,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    max_tokens=max_tokens,
                )
                return result

            except Exception as e:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                error_msg = (
                    f"[{timestamp}] {agent_name} — Attempt {attempt}/{self.max_retries} "
                    f"failed: {type(e).__name__}: {e}"
                )
                _warn(f"Attempt {attempt}/{self.max_retries} failed: {type(e).__name__}: {e}")
                self.errors[agent_name] = error_msg

                if attempt < self.max_retries:
                    _step(f"Retrying in {self.retry_delay}s...")
                    time.sleep(self.retry_delay)

        _fail(
            f"{agent_name} failed after {self.max_retries} attempts. Skipping."
        )
        return None
