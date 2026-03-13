#!/usr/bin/env python3
"""
Multi-Agent Debate System — Main Entry Point
=============================================
Takes a question or idea from the user and runs it through 11 specialized
AI agents that explore, debate, and converge on the best solution.

Usage:
    python main.py
    python main.py --idea "Your question here"
"""

import sys
import os
import argparse
import logging

from dotenv import load_dotenv

from api_client import load_config
from pipeline import Pipeline
from output_formatter import format_output, save_output


def setup_logging():
    """Configure logging to console with timestamps."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def get_idea(args) -> str:
    """Get the user's question from CLI args or interactive prompt."""
    if args.idea:
        return args.idea.strip()

    print("\n" + "=" * 60)
    print("  MULTI-AGENT DEBATE SYSTEM")
    print("  Explore, debate, and solve — powered by AI agents")
    print("=" * 60)
    print("\nEnter your question or idea below.")
    print("The agents will explore, debate, and find the best solution.\n")

    idea = input("Your question: ").strip()
    if not idea:
        print("No question provided. Exiting.")
        sys.exit(1)

    return idea


def main():
    # Load .env file for API keys
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    load_dotenv(env_path)

    setup_logging()
    logger = logging.getLogger(__name__)

    # Parse CLI arguments
    parser = argparse.ArgumentParser(
        description="Run a question through 11 AI agents that debate and find the best solution."
    )
    parser.add_argument(
        "--idea",
        type=str,
        default=None,
        help="The question or idea to process. If not provided, you'll be prompted interactively.",
    )
    args = parser.parse_args()

    # Get the idea
    user_idea = get_idea(args)
    logger.info(f"Question received: {user_idea[:100]}...")

    # Load config
    config = load_config()

    # Run the pipeline
    pipeline = Pipeline(config)
    result = pipeline.run(user_idea)

    # Format the output
    markdown = format_output(
        user_idea=user_idea,
        outputs=result["outputs"],
        errors=result["errors"],
        cost_summary=result["cost_summary"],
        elapsed_seconds=result["elapsed_seconds"],
    )

    # Save to file
    filepath = save_output(markdown, config)

    # Print the cost summary
    print("\n" + "=" * 60)
    print("  COST BREAKDOWN")
    print("=" * 60)
    print(result["cost_summary"])
    print(f"\nTotal pipeline time: {result['elapsed_seconds']:.1f}s")
    print(f"Output saved to: {filepath}")
    print("=" * 60)

    # Print errors if any
    if result["errors"]:
        print("\nWarnings — some agents encountered errors:")
        for agent, err in result["errors"].items():
            print(f"  - {agent}: {err}")


if __name__ == "__main__":
    main()
