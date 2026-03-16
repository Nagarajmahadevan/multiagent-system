"""
Pipeline orchestration — runs all agents in sequential order,
with Layer 2 (Debate) executing in parallel for speed.
Debate-and-solve pipeline: agents explore, debate, and converge on the best solution.
"""

import concurrent.futures
import threading
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
from router import classify, get_active_agents

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


# Execution groups: (mode, [agent_names])
# 'sequential' = one after another
# 'parallel'   = all run at the same time
_EXECUTION_GROUPS = [
    ("sequential", ["visionary", "researcher"]),
    ("sequential", ["critic", "defender", "devils_advocate"]),
    ("sequential", ["context_distiller"]),
    ("sequential", ["mediator", "architect"]),
    ("sequential", ["validator", "summarizer"]),
]


class Pipeline:
    """Runs the full agent debate pipeline."""

    def __init__(self, config: dict, on_event=None):
        self.config = config
        self.client = APIClient(config)
        self.cost_tracker = CostTracker(config["pricing"])
        self.pipeline_cfg = config["pipeline"]

        self.max_retries = self.pipeline_cfg.get("max_retries", 3)
        self.retry_delay = self.pipeline_cfg.get("retry_delay_seconds", 5)
        self.continue_on_failure = self.pipeline_cfg.get("continue_on_failure", True)

        # Optional event callback for real-time UI streaming
        self._on_event = on_event

        # Thread safety for parallel debate agents
        self._lock = threading.Lock()

        # Stores the output of each completed agent
        self.outputs: dict[str, str] = {}
        # Stores any errors that occurred
        self.errors: dict[str, str] = {}

    def _emit(self, event: dict):
        """Fire an event to the optional callback (used by the web UI)."""
        if self._on_event:
            try:
                self._on_event(event)
            except Exception:
                pass

    def run(self, user_idea: str, history: list | None = None, language: str = "en") -> dict:
        """
        Execute the full pipeline for a given idea/question.
        Layer 2 (Debate) agents run in parallel; all other layers are sequential.

        history:  list of {q, a} dicts from prior conversation turns.
        language: BCP-47 language code — agents respond in this language.

        Returns:
            dict with keys: outputs, errors, cost_summary, cost_tracker, elapsed_seconds
        """
        self._history  = history or []
        self._language = language or "en"
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

        # ── Complexity routing ────────────────────────────────────────────
        _step("Classifying query complexity...")
        complexity = classify(user_idea, self.config)
        active_agents = get_active_agents(complexity)  # None = full pipeline
        active_set = set(active_agents) if active_agents else set(AGENT_ORDER)
        total_agents = len(active_set)

        _print(f"  Complexity:  {complexity.upper()} — {total_agents} agents will run")
        if active_agents:
            _print(f"  Agents:      {', '.join(active_agents)}")
            skipped = [a for a in AGENT_ORDER if a not in active_set]
            _print(f"  Skipped:     {', '.join(skipped)}")

        self._emit({
            "type": "route",
            "complexity": complexity,
            "active_agents": active_agents or list(AGENT_ORDER),
            "skipped_agents": [a for a in AGENT_ORDER if a not in active_set],
        })

        self._emit({"type": "pipeline_start", "question": user_idea, "total_agents": total_agents})

        idx = 0
        halted = False
        failed_agent_name = None

        for mode, agents_in_group in _EXECUTION_GROUPS:
            if halted:
                break

            # Filter to active agents only
            agents_in_group = [a for a in agents_in_group if a in active_set]
            if not agents_in_group:
                continue

            # Emit layer_start for this group
            layer_name = AGENT_LAYERS[agents_in_group[0]]
            self._emit({"type": "layer_start", "layer": layer_name})

            if mode == "sequential":
                for agent_name in agents_in_group:
                    idx += 1
                    self._run_single_agent(agent_name, user_idea, idx, total_agents, self._history, self._language)
                    if agent_name in self.errors and not self.continue_on_failure:
                        halted = True
                        failed_agent_name = agent_name
                        break

            else:  # parallel
                _print()
                _print(f"  [PARALLEL] Running {len(agents_in_group)} agents simultaneously:")
                for a in agents_in_group:
                    _print(f"    - {AGENT_DISPLAY_NAMES[a]}")

                agent_idx_map = {}
                for agent_name in agents_in_group:
                    idx += 1
                    agent_idx_map[agent_name] = idx

                with concurrent.futures.ThreadPoolExecutor(
                    max_workers=len(agents_in_group)
                ) as executor:
                    future_to_agent = {
                        executor.submit(
                            self._run_single_agent,
                            agent_name,
                            user_idea,
                            agent_idx_map[agent_name],
                            total_agents,
                            self._history,
                            self._language,
                        ): agent_name
                        for agent_name in agents_in_group
                    }
                    for future in concurrent.futures.as_completed(future_to_agent):
                        agent_name = future_to_agent[future]
                        try:
                            future.result()
                        except Exception as exc:
                            logger.error(f"Parallel agent {agent_name} raised: {exc}")

        elapsed = time.time() - start_time

        if halted and failed_agent_name:
            _banner("PIPELINE HALTED — AGENT FAILURE")
            _print(f"  Failed agent: {failed_agent_name}")
            _print(f"  Error: {self.errors.get(failed_agent_name, 'unknown')}")
            _print(f"  Elapsed: {elapsed:.1f}s")
            self._emit({
                "type": "pipeline_failed",
                "failed_agent": failed_agent_name,
                "failed_agent_display": AGENT_DISPLAY_NAMES.get(failed_agent_name, failed_agent_name),
                "error_message": self.errors.get(failed_agent_name, "Unknown error"),
                "elapsed": round(elapsed, 2),
            })
        else:
            _banner("PIPELINE COMPLETED")
            _print(f"  Total time:  {elapsed:.1f}s")
            _print(f"  Total cost:  INR {self.cost_tracker.total_cost_inr:.6f}")
            _print(f"  Agents run:  {len([v for v in self.outputs.values() if v])}/{total_agents} succeeded")
            if self.errors:
                _print(f"  Errors:      {len(self.errors)} agent(s) had issues")
            self._emit({
                "type": "pipeline_complete",
                "total_cost_inr": self.cost_tracker.total_cost_inr,
                "total_input_tokens": self.cost_tracker.total_input_tokens,
                "total_output_tokens": self.cost_tracker.total_output_tokens,
                "elapsed": round(elapsed, 2),
            })

        return {
            "outputs": self.outputs,
            "errors": self.errors,
            "cost_summary": self.cost_tracker.get_summary_table(),
            "cost_tracker": self.cost_tracker,
            "elapsed_seconds": elapsed,
        }

    # ─────────────────────────────────────────────────────────────────────
    # Single agent execution (used by both sequential and parallel paths)
    # ─────────────────────────────────────────────────────────────────────

    def _run_single_agent(
        self, agent_name: str, user_idea: str, idx: int, total_agents: int,
        history: list | None = None, language: str = "en",
    ) -> None:
        """
        Run one agent end-to-end: build prompt → call API → record cost → emit events.
        Thread-safe: can be called concurrently from parallel debate group.
        """
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

        system_prompt = SYSTEM_PROMPTS[agent_name]

        # Snapshot current outputs at call time (thread-safe read)
        with self._lock:
            current_outputs = dict(self.outputs)

        user_prompt = build_user_prompt(agent_name, user_idea, current_outputs, history=history, language=language)

        _step(f"Calling {provider} API ({model})...")
        agent_start = time.time()

        self._emit({
            "type": "agent_start",
            "agent": agent_name,
            "display": display,
            "layer": layer,
            "model": model,
            "provider": provider,
            "index": idx,
            "total": total_agents,
        })

        result = self._call_with_retries(agent_name, system_prompt, user_prompt, token_limit)
        agent_elapsed = time.time() - agent_start

        if result is not None:
            with self._lock:
                self.outputs[agent_name] = result["content"]
                cost_record = self.cost_tracker.record(
                    agent_name=agent_name,
                    model=result["model"],
                    input_tokens=result["input_tokens"],
                    output_tokens=result["output_tokens"],
                )
            _ok(
                f"{display} completed in {agent_elapsed:.1f}s "
                f"({result['input_tokens']} in / {result['output_tokens']} out tokens)"
            )
            preview = result["content"][:150].replace("\n", " ").strip()
            _print(f"  Preview: {preview}...")
            self._emit({
                "type": "agent_complete",
                "agent": agent_name,
                "display": display,
                "layer": layer,
                "output": result["content"],
                "input_tokens": result["input_tokens"],
                "output_tokens": result["output_tokens"],
                "cost_inr": cost_record.total_cost_inr,
                "elapsed": round(agent_elapsed, 2),
                "model": result["model"],
                "provider": provider,
            })
        else:
            with self._lock:
                self.outputs[agent_name] = ""
            _fail(f"{display} FAILED after {agent_elapsed:.1f}s")
            self._emit({
                "type": "agent_error",
                "agent": agent_name,
                "display": display,
                "error": self.errors.get(agent_name, "Unknown error"),
            })

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
                with self._lock:
                    self.errors[agent_name] = error_msg

                if attempt < self.max_retries:
                    _step(f"Retrying in {self.retry_delay}s...")
                    time.sleep(self.retry_delay)

        _fail(
            f"{agent_name} failed after {self.max_retries} attempts. Skipping."
        )
        return None
