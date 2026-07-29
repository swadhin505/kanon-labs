"""OpenAPI spec -> behavior pack, deterministically.

No LLM in this module. Everything here is *in* the schema: which resources exist,
which operations touch them, what types their fields are, what each operation is
called. Parsing, not inference -- so it either works or it raises, and it can be
tested offline against a real 8 MB spec.

What is deliberately NOT here, because no spec contains it: state transitions.
That is the one irreducible guess, it belongs to a later LLM pass, and until then
a compiled pack has an empty `transitions` and the engine enforces nothing.

Two rules from the coverage probe (LOG.md 4c) shape the whole file:

**Resource identity comes from the response schema, never the URL.** The engine
routes by `operation -> resource + verb`; no path template is involved. So
`POST /charges/{charge}/capture` is an `update` on `charge` because it *returns* a
charge, and `POST /accounts/{account}/people` is a `create` on `person` because it
returns a person. Classifying by path shape gets both wrong.

**Anything we cannot express is flagged, never guessed.** Free-text search and
binary uploads are the only real gaps across Petstore, Twilio and Stripe. They
come back as `Uncovered`, so a hole in the twin is visible instead of silently
faked.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from typing import Any

from kanon.twin.pack import FieldType, Pack

#: Property names that wrap a collection in a list response. Stripe uses `data`.
_LIST_WRAPPERS = ("data", "items", "results", "records", "objects")

#: Query parameters that mean "free-text search", which our equality-only
#: `filter_by` cannot express.
_SEARCH_PARAMS = frozenset({"query", "q", "search", "filter"})

#: Request bodies we cannot model as record fields.
_BINARY_MEDIA = ("multipart/form-data", "application/octet-stream")

_JSON_TYPES: dict[str, FieldType] = {
    "string": "string",
    "number": "number",
    "integer": "integer",
    "boolean": "boolean",
}

_METHODS = ("get", "post", "put", "patch", "delete")

_TAGS = re.compile(r"<[^>]+>")


def _plain(text: str, limit: int = 300) -> str:
    """Strip HTML and collapse whitespace.

    Stripe's descriptions are raw HTML (`<p>`, `<a href=...>`, `<code>`). These end
    up in the tool schemas the agent under test reads, and in the prompt that infers
    transitions, so markup is noise in both places.
    """
    return html.unescape(" ".join(_TAGS.sub(" ", text).split()))[:limit].strip()


@dataclass(frozen=True)
class Uncovered:
    """An operation the pack cannot express, and why. Never silently dropped."""

    operation: str
    path: str
    method: str
    reason: str


@dataclass
class Compiled:
    pack: Pack
    uncovered: list[Uncovered] = field(default_factory=list)
    #: Things a human should look at that are not outright failures.
    notes: list[str] = field(default_factory=list)
    #: resource -> the status values the spec's own enum declares. The states are
    #: read from the spec; only the edges between them ever get inferred.
    states: dict[str, list[str]] = field(default_factory=dict)

    @property
    def coverage(self) -> str:
        total = len(self.pack.routes) + len(self.uncovered)
        return f"{len(self.pack.routes)}/{total}"


# --- spec navigation -----------------------------------------------------


def _deref(spec: dict, node: Any, seen: frozenset[str] = frozenset()) -> Any:
    """Follow a local `$ref`, refusing to loop forever on a cyclic schema."""
    while isinstance(node, dict) and "$ref" in node:
        ref = node["$ref"]
        if not ref.startswith("#/") or ref in seen:
            return {}
        seen = seen | {ref}
        target: Any = spec
        for part in ref[2:].split("/"):
            if not isinstance(target, dict) or part not in target:
                return {}
            target = target[part]
        node = target
    return node if isinstance(node, dict) else {}


def _ref_name(node: Any) -> str | None:
    """Name of the referenced schema, seeing through a live-or-deleted union.

    Stripe returns `anyOf: [customer, deleted_customer]` from a plain GET. Reading
    only a direct `$ref` misses it and reports the operation as uncovered, which
    would put a hole in the twin for one of the commonest resources. The
    `deleted_*` branch is a tombstone, not a separate resource -- the live one is
    what the twin stores.
    """
    if not isinstance(node, dict):
        return None
    if isinstance(node.get("$ref"), str):
        return node["$ref"].rsplit("/", 1)[-1]

    branches = [
        name
        for option in (node.get("anyOf") or node.get("oneOf") or [])
        if (name := _ref_name(option))
    ]
    live = [name for name in branches if not name.startswith("deleted_")]
    return (live or branches or [None])[0]


def _success_schema(spec: dict, operation: dict) -> tuple[str | None, dict, bool]:
    """`(resource name, resolved schema, is_a_list)` for the 2xx JSON response."""
    responses = operation.get("responses") or {}
    for code in ("200", "201", 200, 201, "default"):
        response = _deref(spec, responses.get(code))
        content = response.get("content") or {}
        for media, body in content.items():
            if "json" not in str(media):
                continue
            schema = body.get("schema")
            name = _ref_name(schema)
            resolved = _deref(spec, schema)
            if name and "properties" not in resolved:
                # The union case: `_deref` hands back the `anyOf` wrapper itself,
                # which has no properties. Resolve the branch we picked instead,
                # or the resource compiles with zero fields.
                resolved = _deref(spec, {"$ref": f"#/components/schemas/{name}"})

            # A list response wraps the real resource: {data: [charge]}.
            for wrapper in _LIST_WRAPPERS:
                wrapped = (resolved.get("properties") or {}).get(wrapper)
                items = _deref(spec, wrapped).get("items") if wrapped else None
                if items is not None:
                    item_name = _ref_name(items)
                    if item_name:
                        return item_name, _deref(spec, items), True

            if resolved.get("type") == "array":
                item_name = _ref_name(resolved.get("items"))
                if item_name:
                    return item_name, _deref(spec, resolved["items"]), True

            return name, resolved, False
    return None, {}, False


def _field_types(spec: dict, schema: dict) -> dict[str, FieldType]:
    """Scalar properties of a resource schema. Objects and arrays are skipped --
    the store holds flat records, and pretending otherwise would be a lie."""
    types: dict[str, FieldType] = {}
    for name, raw in (schema.get("properties") or {}).items():
        prop = _deref(spec, raw)
        declared = prop.get("type")
        if isinstance(declared, list):  # ["string", "null"]
            declared = next((t for t in declared if t != "null"), None)
        if declared is None:
            for option in prop.get("anyOf") or prop.get("oneOf") or []:
                candidate = _deref(spec, option).get("type")
                if candidate in _JSON_TYPES:
                    declared = candidate
                    break
        if declared in _JSON_TYPES:
            types[name] = _JSON_TYPES[declared]
    return types


def _segments(path: str) -> list[str]:
    return [s for s in path.strip("/").split("/") if s]


def _is_param(segment: str) -> bool:
    return segment.startswith("{") and segment.endswith("}")


def _parent_resource(spec: dict, path: str) -> str | None:
    """Resource returned by the longest ancestor path present in the spec.

    Spec-driven rather than a singularisation heuristic: this is what separates
    `POST /charges/{charge}/capture` (parent returns a charge -> update) from
    `POST /accounts/{account}/people` (parent returns an account -> create).
    """
    segments = _segments(path)
    paths = spec.get("paths") or {}
    for cut in range(len(segments) - 1, 0, -1):
        candidate = "/" + "/".join(segments[:cut])
        item = paths.get(candidate)
        if isinstance(item, dict) and "get" in item:
            name, _, is_list = _success_schema(spec, item["get"])
            if name and not is_list:
                return name
    return None


# --- classification ------------------------------------------------------


def _verb(spec: dict, path: str, method: str, resource: str, is_list: bool) -> str:
    segments = _segments(path)
    has_param = any(_is_param(s) for s in segments)

    if is_list:
        return "list"
    if method == "delete":
        return "delete"
    if method == "get":
        return "read"
    if not has_param:
        return "create"
    if _is_param(segments[-1]):
        return "update"
    # Static segment after an ancestor id: same resource as the parent means an
    # action on it (capture, reject); a different resource means a nested create.
    return "update" if _parent_resource(spec, path) == resource else "create"


def _id_field(types: dict[str, FieldType], resource: str) -> str:
    if "id" in types:
        return "id"
    for candidate in (f"{resource}_id", "name", "sid"):
        if candidate in types:
            return candidate
    return "id"


def _state_field(schema: dict) -> str | None:
    """A `status`/`state` property with an enum. The states are in the spec; the
    edges between them are not, which is exactly the compiler's boundary."""
    for name in ("status", "state"):
        prop = schema.get("properties", {}).get(name)
        if isinstance(prop, dict) and prop.get("enum"):
            return name
    return None


def _unsupported(spec: dict, operation: dict) -> str | None:
    body = operation.get("requestBody") or {}
    for media in body.get("content") or {}:
        if any(binary in str(media) for binary in _BINARY_MEDIA):
            return "binary or multipart request body"
    for parameter in operation.get("parameters") or []:
        resolved = _deref(spec, parameter)
        if (
            resolved.get("in") == "query"
            and str(resolved.get("name", "")).lower() in _SEARCH_PARAMS
        ):
            return "free-text search, which equality filters cannot express"
    return None


# --- the compiler --------------------------------------------------------


def compile_spec(spec: dict, tools: list[str] | None = None, name: str | None = None) -> Compiled:
    """Compile the operations in `tools` (or all of them) into a pack.

    `tools` is the agent's tool list. Scoping to it is the difference between a
    reviewable pack and an unreviewable one: Stripe publishes 587 operations and
    an agent calls a dozen. Coverage is then an honest fraction with a small
    denominator.
    """
    wanted = set(tools) if tools else None
    resources: dict[str, dict[str, Any]] = {}
    routes: dict[str, dict[str, Any]] = {}
    uncovered: list[Uncovered] = []
    notes: list[str] = []
    states_seen: dict[str, list[str]] = {}

    for path, item in (spec.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        for method in _METHODS:
            operation = item.get(method)
            if not isinstance(operation, dict):
                continue

            operation_id = operation.get("operationId")
            if not operation_id:
                uncovered.append(Uncovered("", path, method, "no operationId to bind a tool to"))
                continue
            if wanted is not None and operation_id not in wanted:
                continue

            reason = _unsupported(spec, operation)
            if reason:
                uncovered.append(Uncovered(operation_id, path, method, reason))
                continue

            resource, schema, is_list = _success_schema(spec, operation)
            if not resource:
                uncovered.append(
                    Uncovered(operation_id, path, method, "no named JSON response schema")
                )
                continue

            types = _field_types(spec, schema)
            entry = resources.setdefault(resource, {"fields": {}})
            entry["fields"].update(types)
            entry.setdefault("id_field", _id_field(entry["fields"], resource))
            state_field = _state_field(schema)
            if state_field:
                entry["state_field"] = state_field
                enum = schema["properties"][state_field].get("enum") or []
                states_seen.setdefault(resource, sorted(str(v) for v in enum))

            routes[operation_id] = _route(spec, path, method, operation, resource, is_list, entry)

    if not routes:
        raise ValueError(
            "no operations compiled -- check the tool list matches the spec's operationIds"
        )

    for resource, states in sorted(states_seen.items()):
        notes.append(
            f"{resource}.{resources[resource]['state_field']} has states {states} in the spec, "
            "but no spec states the transitions between them -- these need inferring or authoring"
        )
    notes.append(
        "optional arguments were not populated: only required ones are derivable. "
        "Add `accepts` by hand for optional fields the agent should be able to send."
    )
    notes.append("`seed` is empty: a spec describes shape, not data.")

    pack = Pack.model_validate(
        {
            "name": name or spec.get("info", {}).get("title", "compiled"),
            "resources": resources,
            "routes": routes,
        }
    )
    return Compiled(pack, uncovered, notes, states_seen)


def _route(
    spec: dict,
    path: str,
    method: str,
    operation: dict,
    resource: str,
    is_list: bool,
    entry: dict[str, Any],
) -> dict[str, Any]:
    verb = _verb(spec, path, method, resource, is_list)
    parameters = [_deref(spec, p) for p in operation.get("parameters") or []]
    path_params = [str(p["name"]) for p in parameters if p.get("in") == "path" and p.get("name")]

    route: dict[str, Any] = {"resource": resource, "verb": verb}
    description = operation.get("summary") or operation.get("description")
    if description and (clean := _plain(str(description))):
        route["description"] = clean

    requires: list[str] = []
    if verb in ("read", "update", "delete") and path_params:
        # The id is the last path param; any earlier ones scope it to a parent.
        if path_params[-1] != entry["id_field"]:
            route["id_param"] = path_params[-1]
        requires += path_params[:-1]
    else:
        requires += path_params

    body = operation.get("requestBody") or {}
    for media, content in (body.get("content") or {}).items():
        if "json" not in str(media) and "urlencoded" not in str(media):
            continue
        schema = _deref(spec, content.get("schema"))
        for name in schema.get("required") or []:
            if name not in requires:
                requires.append(str(name))
        break

    if requires:
        route["requires"] = requires
    if verb == "list":
        filters = [
            str(p["name"])
            for p in parameters
            if p.get("in") == "query" and p.get("name") in entry["fields"]
        ]
        if filters:
            route["filter_by"] = filters
    return route
