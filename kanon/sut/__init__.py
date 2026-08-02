"""The system under test: the agent being measured, never the measurement.

The only run-time LLM in the product lives here. Delete this package and the
twin and the gate still work.
"""

from kanon.sut.external import ExternalAgent, RemoteTwin
from kanon.sut.llm import DEFAULT_MAX_TURNS, DEFAULT_MODEL, LLMAgent
from kanon.sut.tools import openai_tools, tool_schemas

__all__ = [
    "DEFAULT_MAX_TURNS",
    "DEFAULT_MODEL",
    "ExternalAgent",
    "LLMAgent",
    "RemoteTwin",
    "openai_tools",
    "tool_schemas",
]
