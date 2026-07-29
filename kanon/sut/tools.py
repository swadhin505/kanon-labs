"""Turning a behavior pack into tool definitions for the agent under test.

Provider-neutral: the schemas here are plain JSON Schema, which is what every
tool-calling API consumes. Only the wrapper shape differs per provider, and
that lives in one function at the bottom.

The agent sees exactly the operations the pack declares -- no more. That matters
for the honesty rule: an agent cannot be blamed for failing to call something it
was never told about, and it cannot call something the twin does not cover.
"""

from __future__ import annotations

from typing import Any

from kanon.twin.pack import Pack

#: Fallback text when a route has no description. Better than an empty string,
#: worse than a real one -- packs should describe their operations.
_FALLBACK = {
    "list": "List {resource} records.",
    "read": "Fetch one {resource} by id.",
    "create": "Create a {resource}.",
    "update": "Update a {resource}.",
    "delete": "Delete a {resource}.",
}


def tool_schemas(pack: Pack) -> list[dict[str, Any]]:
    """One JSON Schema per operation, ordered so runs are reproducible."""
    schemas = []
    for operation in sorted(pack.routes):
        route = pack.routes[operation]
        arguments = pack.arguments(operation)
        required = pack.required_args(operation)

        schemas.append(
            {
                "name": operation,
                "description": route.description
                or _FALLBACK[route.verb].format(resource=route.resource),
                "parameters": {
                    "type": "object",
                    "properties": {
                        name: {"type": kind} for name, kind in sorted(arguments.items())
                    },
                    "required": required,
                    "additionalProperties": False,
                },
            }
        )
    return schemas


def openai_tools(pack: Pack) -> list[dict[str, Any]]:
    """The OpenAI chat-completions wrapper around the same schemas."""
    return [{"type": "function", "function": schema} for schema in tool_schemas(pack)]
