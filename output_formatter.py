"""
Output formatter — compiles all agent outputs into a structured markdown file
and saves it to the configured output folder.
"""

import os
import logging
from datetime import datetime

from agents import AGENT_ORDER, AGENT_DISPLAY_NAMES, AGENT_LAYERS

logger = logging.getLogger(__name__)

# Section headers for each agent in the final output
SECTION_HEADERS = {
    "visionary": "Visionary — Expanded Approaches",
    "researcher": "Researcher — Real-World Evidence",
    "critic": "Critic — Stress Test",
    "defender": "Defender — Rebuttal",
    "devils_advocate": "Devil's Advocate — Alternative Framing",
    "mediator": "Mediator — Synthesis",
    "architect": "Architect — Final Decision & Plan",
    "validator": "Validator — Feasibility, Risks & Quality",
    "summarizer": "Final Summary & Recommendation",
}


def format_output(
    user_idea: str,
    outputs: dict[str, str],
    errors: dict[str, str],
    cost_summary: str,
    elapsed_seconds: float,
) -> str:
    """
    Build the complete markdown document from all agent outputs.

    Returns:
        The full markdown string.
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    sections = []

    # Title
    sections.append("# Multi-Agent Debate System — Final Report")
    sections.append(f"*Generated on {timestamp} | Pipeline took {elapsed_seconds:.1f}s*\n")

    # Original question
    sections.append("---\n## 1. Original Question / Idea\n")
    sections.append(user_idea)

    # Each agent's output
    section_num = 2
    for agent_name in AGENT_ORDER:
        header = SECTION_HEADERS.get(agent_name, AGENT_DISPLAY_NAMES[agent_name])
        layer = AGENT_LAYERS[agent_name]
        sections.append(f"\n---\n## {section_num}. {header}")
        sections.append(f"*{layer} | {AGENT_DISPLAY_NAMES[agent_name]}*\n")

        content = outputs.get(agent_name, "")
        if content:
            sections.append(content)
        else:
            error = errors.get(agent_name, "Unknown error")
            sections.append(
                f"> **This agent did not produce output.**\n> Error: {error}"
            )
        section_num += 1

    # Cost breakdown
    sections.append(f"\n---\n## {section_num}. Cost Breakdown\n")
    sections.append(cost_summary)

    # Errors section (if any)
    if errors:
        section_num += 1
        sections.append(f"\n---\n## {section_num}. Errors Encountered\n")
        for agent_name, error_msg in errors.items():
            sections.append(f"- **{AGENT_DISPLAY_NAMES.get(agent_name, agent_name)}**: {error_msg}")

    sections.append("\n---\n*End of report.*")

    return "\n\n".join(sections)


def save_output(content: str, config: dict) -> str:
    """
    Save the formatted output to the configured output folder.

    Returns:
        The full path of the saved file.
    """
    output_folder = config["pipeline"].get("output_folder", "output")
    output_filename = config["pipeline"].get("output_filename", "final_output")

    # Use absolute path if provided, otherwise resolve relative to project root
    if os.path.isabs(output_folder):
        full_folder = output_folder
    else:
        project_root = os.path.dirname(__file__)
        full_folder = os.path.join(project_root, output_folder)
    os.makedirs(full_folder, exist_ok=True)

    # Add timestamp to filename to avoid overwriting previous runs
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{output_filename}_{timestamp}.md"
    filepath = os.path.join(full_folder, filename)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)

    logger.info(f"Output saved to: {filepath}")
    return filepath
