"""The twin engine: one generic interpreter for any behavior pack.

Written once, never per provider. Given a pack it exposes exactly the
operations the pack declares, enforces the declared state machine, and refuses
-- loudly -- to answer anything the pack does not cover.

Trust rules this module exists to keep:
  * No LLM, no wall clock, no RNG. Timestamps come from the store's step
    counter, so a run's output depends only on the agent's actions.
  * An uncovered operation raises and is recorded in `uncovered`. It never
    returns a plausible-looking empty result. Silent stubs are how an eval
    scores 100% on nothing.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from kanon.twin.pack import Pack, Resource, Route
from kanon.twin.store import MissingRecord, Record, Snapshot, Store

#: Origin of the logical clock. A knob: set it per run to reproduce a dated
#: scenario ("claim filed on the last day of the plan year") without ever
#: reading the real clock.
DEFAULT_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)
_NO_FAULT = object()


class TwinError(Exception):
    """A refusal the twin is confident about, shaped like a provider error."""

    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status

    def as_response(self) -> dict[str, Any]:
        # ponytail: one generic envelope. Move to a per-provider `errors` section
        # in the pack once a real spec's error shape breaks an agent.
        return {"error": {"code": self.code, "message": self.message}}


@dataclass(frozen=True)
class InjectedResponse:
    """A deliberate successful provider response that bypasses schema shaping."""

    status: int
    body: Any


class Twin:
    """A running deterministic twin of the API described by `pack`."""

    def __init__(self, pack: Pack, epoch: datetime = DEFAULT_EPOCH) -> None:
        self.pack = pack
        self.epoch = epoch
        self.store = Store()
        #: operation id -> times called, for operations the pack does not cover.
        #: Surfaced as the coverage banner; never swallowed.
        self.uncovered: dict[str, int] = {}
        self._events: list[dict[str, Any]] = []
        self.reset()

    # --- control plane ---------------------------------------------------

    def reset(
        self,
        given: dict[str, list[Record]] | None = None,
        faults: list[dict[str, Any]] | None = None,
    ) -> None:
        """Return to the seeded state. Cheap enough to call between trials.

        `uncovered` deliberately survives: it is a coverage log for the whole
        session, not part of the world. Clearing it every trial would hide the
        holes the last trial found.
        """
        resources = self.pack.resources
        if given:
            candidate = self.pack.model_dump()
            for name, patches in given.items():
                if name not in self.pack.resources:
                    raise TwinError(
                        "invalid_given", f"story given names unknown resource {name!r}", status=422
                    )
                resource = self.pack.resources[name]
                records = {
                    str(record[resource.id_field]): deepcopy(record)
                    for record in resource.seed
                }
                for patch in patches:
                    if resource.id_field not in patch:
                        raise TwinError(
                            "invalid_given",
                            f"story given {name} record is missing {resource.id_field!r}",
                            status=422,
                        )
                    record_id = str(patch[resource.id_field])
                    records[record_id] = {**records.get(record_id, {}), **deepcopy(patch)}
                candidate["resources"][name]["seed"] = [
                    records[record_id] for record_id in sorted(records)
                ]
            try:
                resources = Pack.model_validate(candidate).resources
            except ValueError as exc:
                raise TwinError("invalid_given", str(exc), status=422) from None

        checked_faults: dict[str, dict[int, dict[str, Any]]] = {}
        for raw in faults or []:
            operation = raw.get("operation")
            on_call = raw.get("on_call", 1)
            status = raw.get("status", 503)
            if operation not in self.pack.routes:
                raise TwinError(
                    "invalid_fault", f"fault names unknown operation {operation!r}", status=422
                )
            if not isinstance(on_call, int) or isinstance(on_call, bool) or on_call < 1:
                raise TwinError("invalid_fault", "fault on_call must be a positive integer", 422)
            if not isinstance(status, int) or isinstance(status, bool) or not 100 <= status <= 599:
                raise TwinError("invalid_fault", "fault status must be between 100 and 599", 422)
            by_call = checked_faults.setdefault(operation, {})
            if on_call in by_call:
                raise TwinError(
                    "invalid_fault",
                    f"duplicate fault for {operation} call {on_call}",
                    status=422,
                )
            by_call[on_call] = deepcopy(raw)

        self.store = Store()
        self._events = []
        self._faults = checked_faults
        self._call_counts: dict[str, int] = {}
        for name, resource in resources.items():
            self.store.load(name, resource.id_field, resource.seed)

    def begin_run(self) -> None:
        """Clear session-level coverage before a new measured run."""
        self.uncovered = {}
        self.reset()

    def events(self, after: int = 0) -> list[dict[str, Any]]:
        """Calls already applied to this twin, for out-of-process SUTs."""
        return deepcopy(self._events[max(after, 0) :])

    def snapshot(self) -> Snapshot:
        return self.store.snapshot()

    def restore(self, snapshot: Snapshot) -> None:
        rollback = self.store.snapshot()
        self.store.restore(snapshot)
        try:
            self._validate_references()
        except TwinError:
            self.store.restore(rollback)
            raise

    def state(self) -> dict[str, dict[str, Record]]:
        """The full record state, for the scorer to diff."""
        return self.store.state()

    # --- the one entry point ---------------------------------------------

    def call(self, operation: str, args: dict[str, Any] | None = None) -> Any:
        """Execute a tool call. Raises :class:`TwinError` on any refusal."""
        args = dict(args or {})
        try:
            injected = self._inject(operation)
            result = self._call(operation, dict(args)) if injected is _NO_FAULT else injected
        except TwinError as refusal:
            self._events.append(
                {
                    "operation": operation,
                    "args": deepcopy(args),
                    "result": refusal.as_response(),
                    "error": refusal.code,
                    "state": self.state(),
                }
            )
            raise
        event_result = result.body if isinstance(result, InjectedResponse) else result
        self._events.append(
            {
                "operation": operation,
                "args": deepcopy(args),
                "result": deepcopy(event_result),
                # ponytail: full state per call keeps temporal scoring exact for
                # remote agents; store diffs instead if trace size shows up in a profile.
                "state": self.state(),
            }
        )
        return result

    def _inject(self, operation: str) -> Any:
        """Return a scheduled response, or a sentinel when this call is normal."""
        self._call_counts[operation] = self._call_counts.get(operation, 0) + 1
        fault = self._faults.get(operation, {}).get(self._call_counts[operation])
        if fault is None:
            return _NO_FAULT
        status = fault.get("status", 503)
        if status >= 400:
            raise TwinError(
                str(fault.get("code", "injected_failure")),
                str(fault.get("message", "injected provider failure")),
                status,
            )
        return InjectedResponse(status, deepcopy(fault.get("body")))

    def _call(self, operation: str, args: dict[str, Any]) -> Any:
        route = self.pack.routes.get(operation)
        if route is None:
            self.uncovered[operation] = self.uncovered.get(operation, 0) + 1
            raise TwinError(
                "unsupported_operation",
                f"{operation!r} is not covered by the {self.pack.name} behavior pack",
                status=501,
            )

        missing = [name for name in self.pack.required_args(operation) if args.get(name) is None]
        if missing:
            raise TwinError(
                "missing_parameter", f"{operation} requires {', '.join(missing)}", status=422
            )

        expected = self.pack.arguments(operation)
        unexpected = sorted(set(args) - set(expected))
        if unexpected:
            raise TwinError(
                "unexpected_parameter",
                f"{operation} does not accept {', '.join(unexpected)}",
                status=422,
            )
        invalid = [
            name
            for name, value in args.items()
            if value is not None and not _is_type(value, expected[name])
        ]
        if invalid:
            details = ", ".join(f"{name} must be {expected[name]}" for name in invalid)
            raise TwinError("invalid_parameter", f"{operation}: {details}", status=422)

        resource = self.pack.resources[route.resource]
        # A compiled pack may name the id argument differently per operation
        # (Stripe: `{charge}` here, `{id}` there, same resource). Normalise to
        # the resource's own id_field so the verbs below stay simple.
        id_argument = self.pack.id_argument(operation)
        if id_argument != resource.id_field:
            supplied = args.pop(id_argument, None)
            if supplied is not None:
                args.setdefault(resource.id_field, supplied)

        handler = getattr(self, f"_{route.verb}")

        # Writes are all-or-nothing. An operation with effects touches several
        # records, and a refusal partway through must leave none of them
        # changed -- a twin that half-applies a transfer is worse than no twin.
        # ponytail: rollback is a whole-store snapshot; same upgrade path as
        # store.snapshot if it ever shows up in a profile.
        rollback = self.store.snapshot() if route.verb != "list" else None
        try:
            return handler(route, resource, args)
        except MissingRecord as exc:
            if rollback:
                self.store.restore(rollback)
            raise TwinError("not_found", str(exc), status=404) from None
        except TwinError:
            if rollback:
                self.store.restore(rollback)
            raise
        except Exception:
            if rollback:
                self.store.restore(rollback)
            raise

    # --- verbs -----------------------------------------------------------

    def _list(self, route: Route, resource: Resource, args: dict[str, Any]) -> list[Record]:
        records = self.store.list(route.resource)
        for field in route.filter_by:
            if args.get(field) is not None:
                records = [r for r in records if r.get(field) == args[field]]
        return records

    def _read(self, route: Route, resource: Resource, args: dict[str, Any]) -> Record:
        return self.store.require(route.resource, args[resource.id_field])

    def _create(self, route: Route, resource: Resource, args: dict[str, Any]) -> Record:
        record = self._payload(resource, args)
        record[resource.id_field] = self.store.next_id(route.resource, resource.id_prefix)
        if route.sets_state:
            record[resource.state_field] = route.sets_state
        for field in resource.timestamps:
            record[field] = self._now()
        self._check_references(resource, record)
        written = self.store.put(route.resource, record[resource.id_field], record)
        self._apply_effects(route, written)
        return written

    def _update(self, route: Route, resource: Resource, args: dict[str, Any]) -> Record:
        record_id = str(args[resource.id_field])
        record = self.store.require(route.resource, record_id)
        if route.sets_state:
            self._check_transition(route.resource, resource, record, route.sets_state)
        record.update(self._payload(resource, args))
        if route.sets_state:
            record[resource.state_field] = route.sets_state
        self._check_references(resource, record)
        written = self.store.put(route.resource, record_id, record)
        self._apply_effects(route, written)
        return written

    def _delete(self, route: Route, resource: Resource, args: dict[str, Any]) -> Record:
        record_id = str(args[resource.id_field])
        held_by = self._referrers(route.resource, record_id)
        if held_by:
            raise TwinError(
                "reference_in_use",
                f"{route.resource} {record_id} is still referenced by {', '.join(held_by)}",
                status=409,
            )
        return self.store.delete(route.resource, record_id)

    # --- helpers ---------------------------------------------------------

    def _check_references(self, resource: Resource, record: Record) -> None:
        """Every reference on a written record must identify a real record."""
        for field, target in resource.references.items():
            value = record.get(field)
            if value is None:
                continue
            if self.store.get(target, str(value)) is None:
                raise TwinError(
                    "unknown_reference",
                    f"{field} {value!r} does not identify an existing {target}",
                    status=404,
                )

    def _validate_references(self) -> None:
        """Reject restored state containing an orphan without replacing good state."""
        for name, resource in self.pack.resources.items():
            for record in self.store.list(name):
                self._check_references(resource, record)

    def _referrers(self, resource_name: str, record_id: str) -> list[str]:
        """Records pointing at this one. Deleting it would orphan them.

        ponytail: scans every record of every referencing resource. Deletes are
        rare and packs are small; index by (resource, field, value) if a domain
        ever makes this show up in a profile.
        """
        held_by = []
        for name, other in sorted(self.pack.resources.items()):
            fields = [
                field for field, target in other.references.items() if target == resource_name
            ]
            if not fields:
                continue
            for record in self.store.list(name):
                if any(
                    (value := record.get(field)) is not None and str(value) == record_id
                    for field in fields
                ):
                    held_by.append(f"{name} {record[other.id_field]}")
        return held_by

    def _apply_effects(self, route: Route, record: Record) -> None:
        """Apply this operation's changes to other records.

        Both the target id and the value come from the record just written, so
        posting a transfer can move the amount that was recorded when it was
        requested. Any refusal raises, and `call` rolls the whole thing back.
        """
        for effect in route.effects:
            target_id = record.get(effect.id_from)
            if target_id is None:
                raise TwinError(
                    "missing_parameter",
                    f"{effect.id_from} is needed to apply an effect on {effect.resource}",
                    status=422,
                )

            target = self.store.get(effect.resource, str(target_id))
            if target is None:
                raise TwinError(
                    "not_found", f"{effect.resource} {target_id!r} not found", status=404
                )

            value = record.get(effect.value_from)
            if not isinstance(value, int | float) or isinstance(value, bool):
                raise TwinError(
                    "invalid_value",
                    f"{effect.value_from} must be a number to apply to {effect.field}",
                    status=422,
                )

            current = target.get(effect.field) or 0
            if not isinstance(current, int | float) or isinstance(current, bool):
                raise TwinError(
                    "invalid_value",
                    f"{effect.resource}.{effect.field} must be a number",
                    status=422,
                )
            if effect.op == "add":
                updated = current + value
            elif effect.op == "subtract":
                updated = current - value
            else:
                updated = value

            if effect.min is not None and updated < effect.min:
                raise TwinError(
                    effect.error,
                    f"{effect.resource} {target_id} {effect.field} would go to "
                    f"{updated}, below the minimum of {effect.min}",
                    status=409,
                )

            target[effect.field] = updated
            self._check_references(self.pack.resources[effect.resource], target)
            self.store.put(effect.resource, str(target_id), target)

    def _payload(self, resource: Resource, args: dict[str, Any]) -> dict[str, Any]:
        """Persist only declared response fields, never request-only controls."""
        server_owned = {resource.id_field, resource.state_field, *resource.timestamps}
        return {k: v for k, v in args.items() if k in resource.fields and k not in server_owned}

    def _check_transition(self, name: str, resource: Resource, record: Record, target: str) -> None:
        current = record.get(resource.state_field)
        if target not in resource.transitions.get(current, []):
            raise TwinError(
                "invalid_transition",
                f"{name} {record[resource.id_field]} cannot move from {current!r} to {target!r}",
                status=409,
            )

    def _now(self) -> str:
        """The logical clock: epoch advanced by one second per write so far."""
        stamp = self.epoch + timedelta(seconds=self.store.step)
        return stamp.isoformat().replace("+00:00", "Z")


def _is_type(value: Any, expected: str) -> bool:
    """JSON scalar validation without Python's ``bool``-is-an-``int`` trap."""
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, int | float) and not isinstance(value, bool)
    return isinstance(value, str)
