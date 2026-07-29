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

from datetime import UTC, datetime, timedelta
from typing import Any

from kanon.twin.pack import Pack, Resource, Route
from kanon.twin.store import MissingRecord, Record, Snapshot, Store

#: Origin of the logical clock. A knob: set it per run to reproduce a dated
#: scenario ("claim filed on the last day of the plan year") without ever
#: reading the real clock.
DEFAULT_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)


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


class Twin:
    """A running deterministic twin of the API described by `pack`."""

    def __init__(self, pack: Pack, epoch: datetime = DEFAULT_EPOCH) -> None:
        self.pack = pack
        self.epoch = epoch
        self.store = Store()
        #: operation id -> times called, for operations the pack does not cover.
        #: Surfaced as the coverage banner; never swallowed.
        self.uncovered: dict[str, int] = {}
        self.reset()

    # --- control plane ---------------------------------------------------

    def reset(self) -> None:
        """Return to the seeded state. Cheap enough to call between trials.

        `uncovered` deliberately survives: it is a coverage log for the whole
        session, not part of the world. Clearing it every trial would hide the
        holes the last trial found.
        """
        self.store = Store()
        for name, resource in self.pack.resources.items():
            self.store.load(name, resource.id_field, resource.seed)

    def snapshot(self) -> Snapshot:
        return self.store.snapshot()

    def restore(self, snapshot: Snapshot) -> None:
        self.store.restore(snapshot)

    def state(self) -> dict[str, dict[str, Record]]:
        """The full record state, for the scorer to diff."""
        return self.store.state()

    # --- the one entry point ---------------------------------------------

    def call(self, operation: str, args: dict[str, Any] | None = None) -> Any:
        """Execute a tool call. Raises :class:`TwinError` on any refusal."""
        args = dict(args or {})
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
        written = self.store.put(route.resource, record_id, record)
        self._apply_effects(route, written)
        return written

    def _delete(self, route: Route, resource: Resource, args: dict[str, Any]) -> Record:
        return self.store.delete(route.resource, str(args[resource.id_field]))

    # --- helpers ---------------------------------------------------------

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
            updated = {"add": current + value, "subtract": current - value, "set": value}[effect.op]

            if effect.min is not None and updated < effect.min:
                raise TwinError(
                    effect.error,
                    f"{effect.resource} {target_id} {effect.field} would go to "
                    f"{updated}, below the minimum of {effect.min}",
                    status=409,
                )

            target[effect.field] = updated
            self.store.put(effect.resource, str(target_id), target)

    def _payload(self, resource: Resource, args: dict[str, Any]) -> dict[str, Any]:
        """Caller-supplied fields, minus the ones only the twin may set."""
        server_owned = {resource.id_field, resource.state_field, *resource.timestamps}
        return {k: v for k, v in args.items() if k not in server_owned}

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
