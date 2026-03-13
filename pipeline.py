"""
Pipeline orchestration — runs all agents in sequential order with
a build-test-fix loop after Code Reviewer and integration testing
before DevOps. Handles retries, error logging, and context passing.
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
    build_fixer_prompt,
    build_integration_test_prompt,
)
from api_client import APIClient
from cost_tracker import CostTracker
from code_executor import CodeExecutor

logger = logging.getLogger(__name__)


class Pipeline:
    """Runs the full agent pipeline including build-test-fix loops."""

    def __init__(self, config: dict):
        self.config = config
        self.client = APIClient(config)
        self.cost_tracker = CostTracker(config["pricing"])
        self.pipeline_cfg = config["pipeline"]
        self.exec_cfg = config.get("code_execution", {})

        self.max_retries = self.pipeline_cfg.get("max_retries", 3)
        self.retry_delay = self.pipeline_cfg.get("retry_delay_seconds", 5)
        self.continue_on_failure = self.pipeline_cfg.get("continue_on_failure", True)

        self.enable_code_testing = self.exec_cfg.get("enable_code_testing", True)
        self.enable_integration = self.exec_cfg.get("enable_integration_tests", True)
        self.max_fix_attempts = self.exec_cfg.get("max_fix_attempts", 3)
        self.max_integration_fix = self.exec_cfg.get("max_integration_fix_attempts", 3)
        self.localhost_port = self.exec_cfg.get("localhost_port", 8000)

        self.executor = CodeExecutor(config)

        # Stores the output of each completed agent
        self.outputs: dict[str, str] = {}
        # Stores any errors that occurred
        self.errors: dict[str, str] = {}

    def run(self, user_idea: str) -> dict:
        """
        Execute the full pipeline for a given idea.

        Returns:
            dict with keys: outputs, errors, cost_summary, cost_tracker, elapsed_seconds
        """
        logger.info("=" * 60)
        logger.info("PIPELINE STARTED")
        logger.info(f"Idea: {user_idea[:100]}...")
        logger.info("=" * 60)

        start_time = time.time()

        for agent_name in AGENT_ORDER:
            display = AGENT_DISPLAY_NAMES[agent_name]
            layer = AGENT_LAYERS[agent_name]
            token_limit = self.config["token_limits"].get(agent_name, 500)

            logger.info("")
            logger.info(f"--- [{layer}] {display} ---")

            # After code_reviewer, run the build-test-fix loop
            if agent_name == "devops" and self.enable_code_testing:
                self._run_build_test_fix_loop(user_idea)

                if self.enable_integration:
                    self._run_integration_test_loop(user_idea)

            # Run the regular agent
            system_prompt = SYSTEM_PROMPTS[agent_name]
            user_prompt = build_user_prompt(agent_name, user_idea, self.outputs)

            result = self._call_with_retries(
                agent_name, system_prompt, user_prompt, token_limit
            )

            if result is not None:
                self.outputs[agent_name] = result["content"]
                self.cost_tracker.record(
                    agent_name=agent_name,
                    model=result["model"],
                    input_tokens=result["input_tokens"],
                    output_tokens=result["output_tokens"],
                )
                logger.info(f"{display} completed ({result['output_tokens']} tokens)")
            else:
                self.outputs[agent_name] = ""
                logger.error(f"{display} FAILED — output will be empty")

                if not self.continue_on_failure:
                    logger.error("Pipeline halted due to agent failure.")
                    break

        elapsed = time.time() - start_time
        logger.info("")
        logger.info("=" * 60)
        logger.info(f"PIPELINE COMPLETED in {elapsed:.1f}s")
        logger.info(f"Total cost: INR {self.cost_tracker.total_cost_inr:.6f}")
        logger.info("=" * 60)

        return {
            "outputs": self.outputs,
            "errors": self.errors,
            "cost_summary": self.cost_tracker.get_summary_table(),
            "cost_tracker": self.cost_tracker,
            "elapsed_seconds": elapsed,
        }

    # ─────────────────────────────────────────────────────────────────────
    # Build-Test-Fix Loop
    # ─────────────────────────────────────────────────────────────────────

    def _run_build_test_fix_loop(self, user_idea: str):
        """
        Extract code, run it, and if it fails, call Code Fixer to fix it.
        Repeats up to max_fix_attempts times.
        """
        logger.info("")
        logger.info("=" * 40)
        logger.info("BUILD-TEST-FIX LOOP")
        logger.info("=" * 40)

        # Use the latest code output (code_reviewer if available, else coder)
        current_code = self.outputs.get("code_reviewer") or self.outputs.get("coder", "")
        if not current_code:
            logger.warning("No code output to test. Skipping build-test-fix loop.")
            return

        for attempt in range(1, self.max_fix_attempts + 1):
            logger.info(f"\n--- Build-Test Attempt {attempt}/{self.max_fix_attempts} ---")

            # Step 1: Extract code to files
            files = self.executor.extract_code_files(current_code)
            if not files:
                logger.warning("Could not extract any code files. Skipping tests.")
                self.outputs["code_test_result"] = "No code files could be extracted."
                return

            self.executor.write_files(files)
            logger.info(f"  Extracted {len(files)} file(s): {list(files.keys())}")

            # Step 2: Install dependencies
            dep_result = self.executor.install_dependencies()
            if not dep_result["success"]:
                logger.warning(f"  Dependency install failed: {dep_result['error']}")

            # Step 3: Run the code
            run_result = self.executor.run_code()

            if run_result["success"]:
                logger.info("  Code runs successfully!")
                self.outputs["code_test_result"] = (
                    f"PASSED (attempt {attempt})\n"
                    f"Output: {run_result['output'][:500]}"
                )
                return  # Success — exit the loop
            else:
                error_msg = run_result["error"]
                logger.warning(f"  Code failed: {error_msg[:200]}")

                if attempt < self.max_fix_attempts:
                    # Call Code Fixer agent
                    logger.info("  Calling Code Fixer agent...")
                    fix_prompt = build_fixer_prompt(current_code, error_msg, attempt)
                    token_limit = self.config["token_limits"].get("code_fixer", 1000)

                    fix_result = self._call_with_retries(
                        "code_fixer",
                        SYSTEM_PROMPTS["code_fixer"],
                        fix_prompt,
                        token_limit,
                    )

                    if fix_result:
                        current_code = fix_result["content"]
                        self.outputs["code_fixer"] = current_code
                        self.cost_tracker.record(
                            agent_name=f"code_fixer (attempt {attempt})",
                            model=fix_result["model"],
                            input_tokens=fix_result["input_tokens"],
                            output_tokens=fix_result["output_tokens"],
                        )
                        # Update code_reviewer output with fixed code for downstream agents
                        self.outputs["code_reviewer"] = current_code
                    else:
                        logger.error("  Code Fixer failed. Stopping fix loop.")
                        break

        # If we get here, all fix attempts failed
        self.outputs["code_test_result"] = (
            f"FAILED after {self.max_fix_attempts} attempts.\n"
            f"Last error: {run_result['error'][:500]}"
        )
        logger.error("Build-test-fix loop exhausted all attempts.")

    # ─────────────────────────────────────────────────────────────────────
    # Integration Test Loop
    # ─────────────────────────────────────────────────────────────────────

    def _run_integration_test_loop(self, user_idea: str):
        """
        Generate integration tests, start the app, run tests, fix if needed.
        """
        logger.info("")
        logger.info("=" * 40)
        logger.info("INTEGRATION TEST LOOP")
        logger.info("=" * 40)

        current_code = self.outputs.get("code_reviewer") or self.outputs.get("coder", "")
        architect_plan = self.outputs.get("architect", "")

        if not current_code:
            logger.warning("No code available. Skipping integration tests.")
            return

        # Step 1: Generate integration test script
        logger.info("  Generating integration tests...")
        test_prompt = build_integration_test_prompt(
            user_idea, architect_plan, current_code, self.localhost_port
        )
        token_limit = self.config["token_limits"].get("integration_test_writer", 800)

        test_result = self._call_with_retries(
            "integration_test_writer",
            SYSTEM_PROMPTS["integration_test_writer"],
            test_prompt,
            token_limit,
        )

        if not test_result:
            logger.error("  Integration Test Writer failed. Skipping integration tests.")
            self.outputs["integration_test_result"] = "Test generation failed."
            return

        test_script = test_result["content"]
        self.outputs["integration_test_writer"] = test_script
        self.cost_tracker.record(
            agent_name="integration_test_writer",
            model=test_result["model"],
            input_tokens=test_result["input_tokens"],
            output_tokens=test_result["output_tokens"],
        )

        # Extract the actual test code from markdown code blocks
        import re
        code_match = re.search(r"```python\s*\S*\s*\n(.*?)```", test_script, re.DOTALL)
        if code_match:
            test_code = code_match.group(1).strip()
        else:
            test_code = test_script  # Use raw output if no code block found

        # Step 2: Run integration tests with fix loop
        for attempt in range(1, self.max_integration_fix + 1):
            logger.info(
                f"\n--- Integration Test Attempt {attempt}/{self.max_integration_fix} ---"
            )

            # Make sure code files are written
            files = self.executor.extract_code_files(current_code)
            if files:
                self.executor.write_files(files)

            # Run integration tests
            int_result = self.executor.run_integration_tests(test_code)

            if int_result["success"]:
                logger.info("  Integration tests PASSED!")
                self.outputs["integration_test_result"] = (
                    f"PASSED (attempt {attempt})\n"
                    f"Output:\n{int_result['output'][:500]}"
                )
                return  # Success
            else:
                error_msg = int_result["error"] or int_result["output"]
                logger.warning(f"  Integration tests failed: {error_msg[:200]}")

                if attempt < self.max_integration_fix:
                    # Call Code Fixer with integration test error
                    logger.info("  Calling Code Fixer for integration issues...")
                    fix_prompt = build_fixer_prompt(
                        current_code,
                        f"Integration test failure:\n{error_msg}\n\nTest script:\n{test_code}",
                        attempt,
                    )
                    token_limit = self.config["token_limits"].get("code_fixer", 1000)

                    fix_result = self._call_with_retries(
                        "code_fixer",
                        SYSTEM_PROMPTS["code_fixer"],
                        fix_prompt,
                        token_limit,
                    )

                    if fix_result:
                        current_code = fix_result["content"]
                        self.outputs["code_fixer"] = current_code
                        self.outputs["code_reviewer"] = current_code
                        self.cost_tracker.record(
                            agent_name=f"code_fixer (integration fix {attempt})",
                            model=fix_result["model"],
                            input_tokens=fix_result["input_tokens"],
                            output_tokens=fix_result["output_tokens"],
                        )
                    else:
                        logger.error("  Code Fixer failed. Stopping integration loop.")
                        break

        # All attempts failed
        self.outputs["integration_test_result"] = (
            f"FAILED after {self.max_integration_fix} attempts.\n"
            f"Last error: {error_msg[:500]}"
        )
        logger.error("Integration test loop exhausted all attempts.")

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
                logger.warning(error_msg)
                self.errors[agent_name] = error_msg

                if attempt < self.max_retries:
                    logger.info(f"Retrying in {self.retry_delay}s...")
                    time.sleep(self.retry_delay)

        logger.error(
            f"{agent_name} failed after {self.max_retries} attempts. Skipping."
        )
        return None
