"""Guide package — orchestrator role (decompose / delegate / accept / arbitrate).

M1.2a-2 暴露:
- prompt.build_messages(trigger_event) → LLM messages list
- parser.parse_llm_output(raw_json, ...) → event list
"""
from harness.guide.prompt import build_messages
from harness.guide.parser import parse_llm_output, ParseError

__all__ = ["build_messages", "parse_llm_output", "ParseError"]
