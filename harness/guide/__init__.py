"""Guide package — orchestrator role.

M1.2a-3 暴露:
- prompt.build_messages(trigger_event) → LLM messages list
- parser.parse_llm_output(raw_json, ...) → event list
- step.guide_step(session_events, trigger_event, ...) → event list (pure function)
"""
from harness.guide.prompt import build_messages, get_guide_model
from harness.guide.parser import parse_llm_output, ParseError
from harness.guide.step import guide_step

__all__ = [
    "build_messages", "get_guide_model",
    "parse_llm_output", "ParseError",
    "guide_step",
]
