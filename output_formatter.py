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
    "visionary": "Visionary's Expanded Concept",
    "critic": "Critic's Challenges and Risks",
    "architect": "Architect's Final Plan",
    "coder": "Working Code (Coder)",
    "code_reviewer": "Reviewed Code (Code Reviewer)",
    "code_test_result": "Code Test Results",
    "integration_test_result": "Integration Test Results",
    "devops": "DevOps Setup Instructions & README",
    "market_researcher": "Market Research Report",
    "business_strategist": "Business Strategy & Go-to-Market Plan",
    "pitch_writer": "Investor Pitch",
    "marketing": "Marketing Copy & Social Posts",
    "seo": "SEO Strategy & Content Plan",
    "qa_reviewer": "QA Review",
    "summarizer": "Full Summary (Summarizer)",
}

# Extra output keys that appear between agents (not in AGENT_ORDER)
EXTRA_OUTPUT_KEYS = ["code_test_result", "integration_test_result"]


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
    sections.append("# Multi-Agent AI System — Final Output Report")
    sections.append(f"*Generated on {timestamp} | Pipeline took {elapsed_seconds:.1f}s*\n")

    # Original idea
    sections.append("---\n## 1. Original User Idea\n")
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

        # Insert test results after code_reviewer
        if agent_name == "code_reviewer":
            for extra_key in EXTRA_OUTPUT_KEYS:
                if extra_key in outputs and outputs[extra_key]:
                    header = SECTION_HEADERS.get(extra_key, extra_key)
                    sections.append(f"\n---\n## {section_num}. {header}")
                    sections.append(f"*Layer 2 — Build (Testing)*\n")
                    sections.append(outputs[extra_key])
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
