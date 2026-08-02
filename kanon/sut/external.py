"""Framework-neutral adapter for agents that execute outside Kanon's runner."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from kanon.gate.runner import Action, ObservedCall, Say
from kanon.gate.story import Story
from kanon.gate.trajectory import Trajectory


class RemoteTwin:
    """Control a served Kanon twin while an external agent uses it over MCP/HTTP."""

    def __init__(self, url: str, token: str | None = None, client: Any = None) -> None:
        self.url = url.rstrip("/")
        self.token = token
        if client is None:
            try:
                import httpx
            except ImportError as exc:  # pragma: no cover - optional extra
                raise RuntimeError("remote agents need `pip install -e .[serve]`") from exc
            client = httpx.Client(base_url=self.url, timeout=120)
        self._client = client
        self._operations: dict[str, str] | None = None

    @property
    def uncovered(self) -> dict[str, int]:
        return self._request("GET", "/__admin/coverage")

    def begin_run(self) -> None:
        self._request("POST", "/__admin/begin")

    def reset(self) -> None:
        self._request("POST", "/__admin/reset")

    def state(self) -> dict:
        return self._request("GET", "/__admin/state")

    def events(self, after: int = 0) -> tuple[list[dict[str, Any]], int]:
        body = self._request("GET", f"/__admin/events?after={after}")
        return body["events"], body["cursor"]

    def call(self, operation: str, args: dict | None = None) -> Any:
        if self._operations is None:
            schema = self._request("GET", "/openapi.json")
            self._operations = {
                body["operationId"]: path
                for path, methods in schema["paths"].items()
                for body in methods.values()
            }
        path = self._operations.get(operation)
        if path is None:
            raise KeyError(f"remote twin does not expose {operation!r}")
        return self._request("POST", path, args or {})

    def _request(self, method: str, path: str, body: Any = None) -> Any:
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        response = self._client.request(method, path, json=body, headers=headers)
        if response.status_code >= 400:
            raise RuntimeError(
                f"remote twin {method} {path}: {response.status_code} {response.text}"
            )
        return response.json()


class ExternalAgent:
    """Score any agent behind a synchronous ``invoke(messages) -> text`` callable.

    The callable is the only framework-specific line: it may call a LangGraph
    graph, an n8n webhook, an A2A client, or a company's own agent endpoint.
    Tool calls are observed from the remote twin, so they are never replayed.
    """

    def __init__(
        self,
        name: str,
        environment: RemoteTwin,
        invoke: Callable[[list[dict[str, str]]], str],
        max_calls: int = 150,
    ) -> None:
        self.name = name
        self.environment = environment
        self.invoke = invoke
        self.max_calls = max_calls
        self.calls_made = 0
        self._queue: list[Action] = []
        self._messages: list[dict[str, str]] = []
        self._seen_users = 0
        self._cursor = 0
        self._pending = False

    def start(self, story: Story) -> None:
        self._queue = []
        self._messages = [{"role": "user", "content": story.goal.strip()}]
        self._seen_users = 0
        self._cursor = 0
        self._pending = True

    def next_action(self, trajectory: Trajectory) -> Action:
        if self._queue:
            return self._queue.pop(0)

        users = [message for _, message in trajectory.messages() if message.role == "user"]
        new = users[self._seen_users :]
        self._messages.extend({"role": "user", "content": message.content} for message in new)
        self._seen_users = len(users)
        self._pending = self._pending or bool(new)
        if not self._pending:
            return None
        if self.calls_made >= self.max_calls:
            self._pending = False
            return Say(f"[run budget of {self.max_calls} external-agent calls is spent]")

        content = self.invoke(list(self._messages))
        if not isinstance(content, str):
            raise TypeError("external agent invoke must return text")
        self.calls_made += 1

        events, self._cursor = self.environment.events(self._cursor)
        self._queue.extend(
            ObservedCall(event["operation"], event["args"], event["result"], event.get("error"))
            for event in events
        )
        content = content.strip()
        if content:
            self._messages.append({"role": "assistant", "content": content})
            self._queue.append(Say(content))
        self._pending = False
        return self._queue.pop(0) if self._queue else None
