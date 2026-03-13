#!/usr/bin/env python3
"""
Multi-Agent AI System — Main Entry Point
=========================================
Takes a raw idea from the user and runs it through 12 specialized AI agents
to produce a complete, launch-ready output including working code,
a business plan, and marketing materials.

Usage:
    python main.py
    python main.py --idea "Your idea here"
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
    """Get the user's idea from CLI args or interactive prompt."""
    if args.idea:
        return args.idea.strip()

    print("\n" + "=" * 60)
    print("  MULTI-AGENT AI SYSTEM")
    print("  From idea to launch-ready package — fully autonomous")
    print("=" * 60)
    print("\nEnter your idea below. Be as detailed or brief as you like.")
    print("The system will expand, challenge, build, and package it.\n")

    idea = input("Your idea: ").strip()
    if not idea:
        print("No idea provided. Exiting.")
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
        description="Run a raw idea through 12 AI agents to produce a launch-ready package."
    )
    parser.add_argument(
        "--idea",
        type=str,
        default=None,
        help="The raw idea to process. If not provided, you'll be prompted interactively.",
    )
    parser.add_argument(
        "--folder",
        type=str,
        default=None,
        help="Folder path where the generated project, tests, and outputs will be created.",
    )
    args = parser.parse_args()

    # Get the idea
    user_idea = get_idea(args)
    logger.info(f"Idea received: {user_idea[:100]}...")

    # Load config
    config = load_config()

    # Resolve project folder from CLI arg
    if args.folder:
        project_folder = os.path.abspath(args.folder)
        config["code_execution"]["project_folder"] = project_folder
        config["pipeline"]["output_folder"] = os.path.join(project_folder, "output")
        logger.info(f"Project folder: {project_folder}")

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
