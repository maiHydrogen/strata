"""Few-shot prompt template for intent extraction.

The examples below are SYNTHETIC PLACEHOLDERS, not real reference-document
excerpts. Swap these for the manually-tagged construction-relevant examples
produced in project_plan.md Phase 1 once that corpus exists.
"""

from __future__ import annotations

SYSTEM_PROMPT = (
    "You are an intent-extraction engine for civil-engineering documents "
    "(Scopes of Work, DPRs, Tenders). Read the document text and extract every "
    "distinct construction intent as a structured record: the action requested, "
    "the object/element it applies to, any specification parameters mentioned "
    "(dimensions, materials, standards referenced), and quantity if given. "
    "Ignore legal/contractual boilerplate (payment terms, liability, dispute "
    "resolution) — extract only construction-relevant intents. "
    "Do not invent values that aren't stated in the text."
)

# Each example pairs a short document excerpt with the intents a correct
# extraction should produce. Kept intentionally small and synthetic.
FEW_SHOT_EXAMPLES: list[dict] = [
    {
        "text": (
            "The contractor shall construct RCC staircases of minimum clear "
            "width 1.2m connecting all floors, in accordance with NBC Part 4."
        ),
        "intents": [
            {
                "action": "construct",
                "object": "staircase",
                "specification": {"material": "RCC", "min_clear_width": "1.2m", "standard": "NBC Part 4"},
                "quantity": None,
            }
        ],
    },
    {
        "text": (
            "Provide 2 nos. fire exit doors of width not less than 1000mm on "
            "the ground floor, opening in the direction of egress."
        ),
        "intents": [
            {
                "action": "provide",
                "object": "fire exit door",
                "specification": {"min_width": "1000mm", "opening_direction": "direction of egress"},
                "quantity": "2 nos.",
            }
        ],
    },
]


def build_prompt(document_text: str) -> str:
    """Assemble the few-shot examples plus the target document into one user prompt."""
    parts = [
        "Here are examples of the extraction task:\n",
    ]
    for i, example in enumerate(FEW_SHOT_EXAMPLES, start=1):
        parts.append(f"Example {i}:\nDocument excerpt:\n{example['text']}\n")
        parts.append(f"Extracted intents:\n{example['intents']}\n")

    parts.append(
        "Now extract intents from the following document. Return only the "
        "intents that are actually stated — do not repeat the examples.\n\n"
        f"Document:\n{document_text}"
    )
    return "\n".join(parts)
