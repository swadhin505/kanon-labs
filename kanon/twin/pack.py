"""The behavior pack: the declarative artifact a spec compiles into.

Data, not generated code. A pack is hand-editable YAML -- that is the
calibration knob for the rules a compiler gets wrong. Everything here is
validated on load, so a bad generated pack fails at build time with a message
naming the field, rather than at run time as a wrong test result.

Schema shape follows the one the mock-server ecosystem converged on
(mockd's ``tables``/``extend``, Mockoon's CRUD routes): resources own the data
and the id strategy, routes bind an operation id to a verb on a resource. What
those tools do not have -- and what makes this a twin rather than a mock -- is
``transitions``: the legal state machine per resource.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, model_validator

Verb = Literal["list", "read", "create", "update", "delete"]
FieldType = Literal["string", "number", "integer", "boolean"]

#: Verbs that address one existing record, and so always need its id.
_NEEDS_ID: frozenset[str] = frozenset({"read", "update", "delete"})
_NUMERIC_TYPES: frozenset[FieldType] = frozenset({"number", "integer"})


def _matches_field_type(value: Any, expected: FieldType) -> bool:
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, int | float) and not isinstance(value, bool)
    return isinstance(value, str)


class Resource(BaseModel):
    """One collection of records, plus the state machine its records obey."""

    model_config = ConfigDict(extra="forbid")

    id_field: str
    id_prefix: str = ""
    #: Field holding the record's state. Required if `transitions` is set.
    state_field: str | None = None
    #: ``from_state -> [legal to_states]``. A state with an empty list is terminal.
    transitions: dict[str, list[str]] = {}
    #: Fields the engine stamps with the logical clock on create.
    timestamps: list[str] = []
    #: Field name -> JSON type. Used to build tool schemas for the agent under
    #: test, so it sends `amount: 800` and not `amount: "800"`. Unlisted fields
    #: are treated as strings. An OpenAPI spec carries exactly this, so the
    #: compiler fills it for free.
    fields: dict[str, FieldType] = {}
    #: Field name -> the resource its value must identify. Without this the twin
    #: happily files a claim for a member who does not exist and returns 200 --
    #: which is the "silent tool-call failure" this product exists to catch, so
    #: reproducing it would be indefensible. A referenced record must exist on
    #: write, and cannot be deleted while something still points at it.
    references: dict[str, str] = {}
    seed: list[dict[str, Any]] = []

    @property
    def states(self) -> set[str]:
        return set(self.transitions)

    @model_validator(mode="after")
    def _check(self) -> Resource:
        if self.transitions and not self.state_field:
            raise ValueError("transitions declared without a state_field")

        # A typo'd target state would otherwise read as "terminal" and silently
        # make a legal transition illegal -- the exact class of generated-pack
        # bug that shows up as a mysterious test failure weeks later.
        unknown = {t for targets in self.transitions.values() for t in targets} - self.states
        if unknown:
            raise ValueError(f"transition targets are not declared states: {sorted(unknown)}")

        for record in self.seed:
            if self.id_field not in record:
                raise ValueError(f"seed record is missing {self.id_field!r}: {record}")
            invalid = [
                field
                for field, expected in self.fields.items()
                if (value := record.get(field)) is not None
                and not _matches_field_type(value, expected)
            ]
            if invalid:
                raise ValueError(f"seed record has invalid field types: {', '.join(invalid)}")
            if self.state_field:
                state = record.get(self.state_field)
                if state not in self.states:
                    raise ValueError(f"seed record has unknown {self.state_field}={state!r}")
        return self


class Effect(BaseModel):
    """A change this operation makes to a *different* record.

    Posting a transfer moves money out of one account and into another; the
    record being written is the transfer, but the balances live elsewhere. One
    operation therefore has to touch several records, and it has to do so
    atomically -- if any effect is refused, none of them happen.
    """

    model_config = ConfigDict(extra="forbid")

    resource: str
    #: Field on the record being written that holds the target record's id.
    id_from: str
    field: str
    op: Literal["add", "subtract", "set"]
    #: Field on the record being written that holds the value to apply.
    value_from: str
    #: Floor for the result. Going below it refuses the whole operation with
    #: `error` -- this is how "insufficient funds" and "out of stock" are
    #: expressed without writing any code.
    min: float | None = None
    error: str = "effect_rejected"


class Route(BaseModel):
    """Binds one operation id (a tool the agent can call) to a verb."""

    model_config = ConfigDict(extra="forbid")

    resource: str
    verb: Verb
    #: What this operation does, in the words the agent under test will read.
    #: Tool descriptions drive agent behaviour more than almost anything else,
    #: and a spec's summary/description maps straight onto this.
    description: str = ""
    effects: list[Effect] = []
    #: Arguments that must be present and non-null. The resource's id_field is
    #: added automatically for read/update/delete.
    requires: list[str] = []
    #: For `list`: arguments that, when supplied, filter results by equality.
    filter_by: list[str] = []
    #: Optional arguments this operation accepts beyond `requires`. Kept
    #: explicit rather than "every writable field", so `submit_claim` cannot
    #: offer `approved_amount` and let an agent file a pre-approved claim.
    accepts: list[str] = []
    #: Per-operation argument types. Request parameters can have a different
    #: shape from the returned resource, so the compiler stores their types on
    #: the route instead of pretending they are persisted resource fields.
    argument_types: dict[str, FieldType] = {}
    #: The state this operation moves the record into. The only way state
    #: changes -- a caller cannot set state_field directly, so there is exactly
    #: one source of truth for every transition.
    sets_state: str | None = None
    #: Argument name carrying the record's id, when it differs from the
    #: resource's `id_field`. Real specs need this: Stripe's
    #: `GET /v1/charges/{charge}` names the param `charge` while the record's own
    #: id property is `id`, and `/v1/application_fees/{id}/refund` uses `id` for
    #: the same resource. The argument name is per-operation, the record key is
    #: per-resource, and they are not the same thing.
    id_param: str | None = None

    @model_validator(mode="after")
    def _check(self) -> Route:
        if self.sets_state and self.verb not in ("create", "update"):
            raise ValueError(f"sets_state is meaningless on verb {self.verb!r}")
        if self.filter_by and self.verb != "list":
            raise ValueError(f"filter_by is meaningless on verb {self.verb!r}")
        if self.effects and self.verb not in ("create", "update"):
            raise ValueError(f"effects are not supported on verb {self.verb!r}")
        return self


class Pack(BaseModel):
    """A complete twin definition: what exists, and what may happen to it."""

    model_config = ConfigDict(extra="forbid")

    name: str
    resources: dict[str, Resource]
    routes: dict[str, Route]

    @model_validator(mode="after")
    def _check(self) -> Pack:
        for operation, route in self.routes.items():
            resource = self.resources.get(route.resource)
            if resource is None:
                raise ValueError(f"route {operation!r} targets unknown resource {route.resource!r}")
            if route.sets_state:
                if not resource.state_field:
                    raise ValueError(
                        f"route {operation!r} sets state on {route.resource!r}, "
                        "which has no state_field"
                    )
                if route.sets_state not in resource.states:
                    raise ValueError(
                        f"route {operation!r} sets undeclared state {route.sets_state!r}"
                    )
            for effect in route.effects:
                target = self.resources.get(effect.resource)
                if target is None:
                    raise ValueError(
                        f"route {operation!r} has an effect on unknown resource {effect.resource!r}"
                    )
                server_owned = {target.id_field, target.state_field, *target.timestamps}
                if effect.field in server_owned:
                    raise ValueError(
                        f"route {operation!r} effect writes server-owned field "
                        f"{effect.resource}.{effect.field}"
                    )
                source_fields = {
                    resource.id_field,
                    resource.state_field,
                    *resource.timestamps,
                    *resource.fields,
                }
                if effect.id_from not in source_fields:
                    raise ValueError(
                        f"route {operation!r} effect reads unknown field {effect.id_from!r}"
                    )
                if resource.fields.get(effect.value_from) not in _NUMERIC_TYPES:
                    raise ValueError(
                        f"route {operation!r} effect value {effect.value_from!r} is not numeric"
                    )
                if target.fields.get(effect.field) not in _NUMERIC_TYPES:
                    raise ValueError(
                        f"route {operation!r} effect target "
                        f"{effect.resource}.{effect.field} is not numeric"
                    )

        for name, resource in self.resources.items():
            if not resource.transitions:
                continue
            routes = {
                operation: route
                for operation, route in self.routes.items()
                if route.resource == name
            }
            if not any(route.sets_state for route in routes.values()):
                raise ValueError(f"resource {name!r} has transitions but no route sets its state")
            missing = sorted(
                operation
                for operation, route in routes.items()
                if route.verb == "create" and not route.sets_state
            )
            if missing:
                raise ValueError(
                    f"create routes for {name!r} do not set an initial state: {', '.join(missing)}"
                )

        self._check_references()
        return self

    def _check_references(self) -> None:
        """References must name real resources, and seed data must satisfy them.

        A seeded orphan would be a lie the engine then enforces on everyone else:
        writes get checked but the starting state never was.
        """
        for name, resource in self.resources.items():
            for field, target in resource.references.items():
                if field not in resource.fields:
                    raise ValueError(
                        f"resource {name!r} references through undeclared field {field!r}"
                    )
                if target not in self.resources:
                    raise ValueError(
                        f"resource {name!r} references unknown resource {target!r} via {field!r}"
                    )
                if field == resource.id_field:
                    raise ValueError(
                        f"resource {name!r} cannot reference through its own id field {field!r}"
                    )

        seeded = {
            name: {str(record[resource.id_field]) for record in resource.seed}
            for name, resource in self.resources.items()
        }
        for name, resource in self.resources.items():
            for record in resource.seed:
                for field, target in resource.references.items():
                    value = record.get(field)
                    if value is not None and str(value) not in seeded[target]:
                        raise ValueError(
                            f"seed {name} {record[resource.id_field]!r} references "
                            f"missing {target} {value!r}"
                        )

    def arguments(self, operation: str) -> dict[str, FieldType]:
        """Every argument this operation accepts, with its JSON type.

        Required args, plus list filters, plus whatever the route explicitly
        `accepts`. Deliberately *not* every writable field on the resource: a
        tool offering arguments its operation has no business taking is noise
        for the agent, and a route into state the workflow forbids.

        This is what a tool schema is built from, so anything missing here is an
        argument the agent cannot send.
        """
        route = self.routes[operation]
        resource = self.resources[route.resource]

        names = list(self.required_args(operation))
        for optional in (route.filter_by, route.accepts):
            names += [name for name in optional if name not in names]

        id_names = {resource.id_field, self.id_argument(operation)}
        return {
            name: (
                route.argument_types.get(name)
                or ("string" if name in id_names else resource.fields.get(name, "string"))
            )
            for name in names
        }

    def id_argument(self, operation: str) -> str:
        """The argument name that carries the record's id for this operation."""
        route = self.routes[operation]
        return route.id_param or self.resources[route.resource].id_field

    def required_args(self, operation: str) -> list[str]:
        """Declared requirements plus the implicit id for record-addressing verbs."""
        route = self.routes[operation]
        required = list(route.requires)
        id_argument = self.id_argument(operation)
        if route.verb in _NEEDS_ID and id_argument not in required:
            required.insert(0, id_argument)
        return required

    @classmethod
    def from_yaml(cls, path: str | Path) -> Pack:
        return cls.model_validate(_read_yaml(Path(path)))

    @classmethod
    def from_layers(cls, *paths: str | Path | None) -> Pack:
        """Merge pack layers in order, later winning. Missing files are skipped.

        The file boundary *is* the provenance boundary, which is why there are
        three of them rather than one annotated file:

            pack.generated.yaml   read out of the spec      -- trustworthy
            pack.inferred.yaml    guessed by an LLM         -- review this
            pack.yaml            written by a human        -- always wins

        You can diff the middle one, or delete it to throw away every guess, and
        re-running either machine step cannot touch the file your edits live in.
        """
        merged: Any = {}
        found = False
        for path in paths:
            if path is None or not Path(path).exists():
                continue
            merged = merge_patch(merged, _read_yaml(Path(path)))
            found = True
        if not found:
            raise FileNotFoundError(f"no pack layer found among {[str(p) for p in paths if p]}")
        return cls.model_validate(merged)


def _read_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def merge_patch(target: Any, patch: Any) -> Any:
    """JSON Merge Patch (RFC 7386): recursive merge where `None` deletes.

    Chosen over inventing an override format, and over the OpenAPI Overlay
    Specification's JSONPath actions, because a pack is three levels deep -- you
    write the subtree you want changed and nothing else. `None` is the documented
    way to delete a key the compiler produced. Overlay-style targeting is the
    upgrade path if anyone ever needs wildcards.
    """
    if not isinstance(patch, dict) or not isinstance(target, dict):
        return patch

    merged = dict(target)
    for key, value in patch.items():
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = merge_patch(merged.get(key), value)
    return merged
