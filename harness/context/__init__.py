"""Context assembly — turn (role, task, history) into LLM messages."""
from harness.context.assemble import (
    assemble_context_for_npc,
    assemble_review_context,
    filter_events_for_review,
)

__all__ = [
    "assemble_context_for_npc",
    "assemble_review_context",
    "filter_events_for_review",
]
