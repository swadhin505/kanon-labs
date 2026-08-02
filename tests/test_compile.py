"""The compiler's runnable check.

The fixture is not a toy: every shape in it was taken from the real Stripe spec
during the coverage probe (LOG.md 4c) -- a `data`-wrapped list, a path param named
after the resource rather than `id`, an action endpoint that returns its own
parent, a nested create that returns a different resource, a free-text search and
a file upload. These are the cases that make path-shape classification wrong, so
they are pinned here permanently.
"""

from __future__ import annotations

import copy

import pytest

from kanon.compile import compile_spec
from kanon.twin import Pack, Twin
from kanon.twin.pack import merge_patch

CHARGE = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "amount": {"type": "integer"},
        "currency": {"type": "string"},
        "captured": {"type": "boolean"},
        "status": {"type": "string", "enum": ["pending", "succeeded", "failed"]},
        "metadata": {"type": "object"},  # skipped: the store holds flat records
        "refunds": {"type": "array", "items": {"type": "string"}},  # skipped
    },
}

PERSON = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "account": {"type": "string"},
        "email": {"type": "string"},
    },
}

ACCOUNT = {
    "type": "object",
    "properties": {"id": {"type": "string"}, "country": {"type": "string"}},
}

CUSTOMER = {
    "type": "object",
    "properties": {"id": {"type": "string"}, "email": {"type": "string"}},
}
DELETED_CUSTOMER = {"type": "object", "properties": {"id": {"type": "string"}}}


def json_body(ref: str) -> dict:
    return {"content": {"application/json": {"schema": {"$ref": ref}}}}


SPEC = {
    "openapi": "3.0.0",
    "info": {"title": "payments"},
    "components": {
        "schemas": {
            "charge": CHARGE,
            "person": PERSON,
            "account": ACCOUNT,
            "customer": CUSTOMER,
            "deleted_customer": DELETED_CUSTOMER,
        }
    },
    "paths": {
        "/v1/charges": {
            "get": {
                "operationId": "ListCharges",
                "summary": "List all charges.",
                "parameters": [
                    {"name": "currency", "in": "query", "schema": {"type": "string"}},
                    {"name": "limit", "in": "query", "schema": {"type": "integer"}},
                ],
                "responses": {
                    "200": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "data": {
                                            "type": "array",
                                            "items": {"$ref": "#/components/schemas/charge"},
                                        }
                                    },
                                }
                            }
                        }
                    }
                },
            },
            "post": {
                "operationId": "PostCharges",
                # Real Stripe descriptions are HTML, entities and all.
                "description": (
                    '<p>Use the <a href="/docs/api/payment_intents">Payment Intents API</a>'
                    " instead. Confirmation creates the <code>Charge</code> object.</p>"
                ),
                "requestBody": {
                    "content": {
                        "application/x-www-form-urlencoded": {
                            "schema": {
                                "type": "object",
                                "required": ["amount", "currency"],
                                "properties": {
                                    "amount": {"type": "integer"},
                                    "currency": {"type": "string"},
                                    "description": {"type": "string"},
                                },
                            }
                        }
                    }
                },
                "responses": {"200": json_body("#/components/schemas/charge")},
            },
        },
        # Path param named after the resource, not `id`.
        "/v1/charges/{charge}": {
            "get": {
                "operationId": "GetChargesCharge",
                "parameters": [{"name": "charge", "in": "path", "required": True}],
                "responses": {"200": json_body("#/components/schemas/charge")},
            },
            "post": {
                "operationId": "PostChargesCharge",
                "parameters": [{"name": "charge", "in": "path", "required": True}],
                "responses": {"200": json_body("#/components/schemas/charge")},
            },
        },
        # Action verb: returns its OWN parent resource -> update, not "other".
        "/v1/charges/{charge}/capture": {
            "post": {
                "operationId": "PostChargesChargeCapture",
                "summary": "Capture an authorised charge.",
                "parameters": [{"name": "charge", "in": "path", "required": True}],
                "responses": {"200": json_body("#/components/schemas/charge")},
            }
        },
        "/v1/accounts/{account}": {
            "get": {
                "operationId": "GetAccountsAccount",
                "parameters": [{"name": "account", "in": "path", "required": True}],
                "responses": {"200": json_body("#/components/schemas/account")},
            }
        },
        # Nested create: returns a DIFFERENT resource than its parent.
        "/v1/accounts/{account}/people": {
            "post": {
                "operationId": "PostAccountsAccountPeople",
                "parameters": [{"name": "account", "in": "path", "required": True}],
                "responses": {"200": json_body("#/components/schemas/person")},
            }
        },
        # Stripe returns a live-or-deleted union from a plain GET.
        "/v1/customers/{customer}": {
            "get": {
                "operationId": "GetCustomersCustomer",
                "parameters": [{"name": "customer", "in": "path", "required": True}],
                "responses": {
                    "200": {
                        "content": {
                            "application/json": {
                                "schema": {
                                    "anyOf": [
                                        {"$ref": "#/components/schemas/customer"},
                                        {"$ref": "#/components/schemas/deleted_customer"},
                                    ]
                                }
                            }
                        }
                    }
                },
            }
        },
        # The two real gaps.
        "/v1/charges/search": {
            "get": {
                "operationId": "GetChargesSearch",
                "parameters": [{"name": "query", "in": "query", "required": True}],
                "responses": {"200": json_body("#/components/schemas/charge")},
            }
        },
        "/v1/files": {
            "post": {
                "operationId": "PostFiles",
                "requestBody": {"content": {"multipart/form-data": {"schema": {"type": "object"}}}},
                "responses": {"200": json_body("#/components/schemas/charge")},
            }
        },
    },
}


@pytest.fixture
def compiled():
    return compile_spec(SPEC)


# --- verbs come from the response schema, not the URL --------------------


def test_an_action_endpoint_is_an_update_on_its_own_resource(compiled) -> None:
    """`POST /charges/{charge}/capture` returns a charge, so it updates a charge.
    Classifying by path shape would call it an unsupported action."""
    route = compiled.pack.routes["PostChargesChargeCapture"]
    assert (route.resource, route.verb) == ("charge", "update")
    assert route.id_param == "charge"


def test_a_nested_create_is_a_create_on_the_child_resource(compiled) -> None:
    """Same URL shape as capture, different resource returned -> create."""
    route = compiled.pack.routes["PostAccountsAccountPeople"]
    assert (route.resource, route.verb) == ("person", "create")
    assert route.requires == ["account"], "the parent id becomes a required argument"


def test_the_other_verbs(compiled) -> None:
    verbs = {name: (r.resource, r.verb) for name, r in compiled.pack.routes.items()}
    assert verbs["ListCharges"] == ("charge", "list")
    assert verbs["PostCharges"] == ("charge", "create")
    assert verbs["GetChargesCharge"] == ("charge", "read")
    assert verbs["PostChargesCharge"] == ("charge", "update")


# --- fields and arguments ------------------------------------------------


def test_field_types_are_read_from_the_schema(compiled) -> None:
    fields = compiled.pack.resources["charge"].fields
    assert fields == {
        "id": "string",
        "amount": "integer",
        "currency": "string",
        "captured": "boolean",
        "status": "string",
    }, "nested object and array properties are skipped, not guessed at"


def test_required_body_fields_become_required_arguments(compiled) -> None:
    assert set(compiled.pack.required_args("PostCharges")) == {"amount", "currency"}
    assert compiled.pack.arguments("PostCharges")["description"] == "string"


def test_the_id_argument_can_differ_from_the_record_key(compiled) -> None:
    """Stripe names the param `{charge}` while the record's key is `id`."""
    assert compiled.pack.resources["charge"].id_field == "id"
    assert compiled.pack.id_argument("GetChargesCharge") == "charge"
    assert compiled.pack.required_args("GetChargesCharge") == ["charge"]


def test_query_parameters_become_filters_only_when_they_are_real_fields(compiled) -> None:
    assert compiled.pack.routes["ListCharges"].filter_by == ["currency"]
    assert "limit" not in compiled.pack.arguments("ListCharges"), "paging is not a field filter"


def test_descriptions_are_carried_through_without_markup(compiled) -> None:
    """Stripe's descriptions are raw HTML, and these land in the tool schemas the
    agent reads and in the transition-inference prompt. Markup is noise in both."""
    assert compiled.pack.routes["ListCharges"].description == "List all charges."
    assert compiled.pack.routes["PostCharges"].description == (
        "Use the Payment Intents API instead. Confirmation creates the Charge object."
    )


# --- honesty -------------------------------------------------------------


def test_a_live_or_deleted_union_response_still_resolves(compiled) -> None:
    """Stripe's `GET /v1/customers/{customer}` returns
    `anyOf: [customer, deleted_customer]`. Reading only a direct `$ref` reported
    it uncovered, putting a hole in the twin for a very common resource; a later
    half-fix found the name but compiled zero fields. Both are pinned here."""
    route = compiled.pack.routes["GetCustomersCustomer"]
    assert (route.resource, route.verb) == ("customer", "read")
    assert compiled.pack.resources["customer"].fields == {"id": "string", "email": "string"}
    assert "deleted_customer" not in compiled.pack.resources, "a tombstone is not a resource"


def test_the_two_real_gaps_are_flagged_not_faked(compiled) -> None:
    gaps = {u.operation: u.reason for u in compiled.uncovered}
    assert "free-text search" in gaps["GetChargesSearch"]
    assert "binary or multipart" in gaps["PostFiles"]
    assert compiled.coverage == "8/10", "10 operations in the spec, 8 expressible"


def test_states_are_found_but_transitions_are_never_invented(compiled) -> None:
    charge = compiled.pack.resources["charge"]
    assert charge.state_field == "status"
    assert charge.transitions == {}, "no spec states the edges; inventing them would be a lie"
    assert any("no spec states the transitions" in note for note in compiled.notes)


def test_an_empty_tool_list_match_is_an_error_not_an_empty_pack() -> None:
    with pytest.raises(ValueError, match="no operations compiled"):
        compile_spec(SPEC, tools=["NoSuchOperation"])


# --- scoping to the agent's tools ----------------------------------------


def test_scoping_to_a_tool_list_shrinks_the_pack() -> None:
    scoped = compile_spec(SPEC, tools=["ListCharges", "PostCharges", "GetChargesCharge"])
    assert set(scoped.pack.routes) == {"ListCharges", "PostCharges", "GetChargesCharge"}
    assert set(scoped.pack.resources) == {"charge"}, "unreferenced resources are not compiled"


def test_missing_requested_tools_are_visible_in_coverage() -> None:
    scoped = compile_spec(SPEC, tools=["PostCharges", "DoesNotExist"])
    assert scoped.coverage == "1/2"
    assert scoped.uncovered[0].operation == "DoesNotExist"
    assert "not found" in scoped.uncovered[0].reason


def test_path_level_parameters_are_not_lost() -> None:
    spec = copy.deepcopy(SPEC)
    item = spec["paths"]["/v1/charges/{charge}"]
    item["parameters"] = item["get"].pop("parameters") + [
        {"name": "tenant", "in": "query", "required": True}
    ]

    pack = compile_spec(spec, tools=["GetChargesCharge"]).pack
    assert pack.required_args("GetChargesCharge") == ["charge", "tenant"]


def test_allof_and_any_2xx_response_are_supported() -> None:
    spec = copy.deepcopy(SPEC)
    operation = spec["paths"]["/v1/charges"]["post"]
    spec["components"]["requestBodies"] = {"charge": operation["requestBody"]}
    operation["requestBody"] = {"$ref": "#/components/requestBodies/charge"}
    operation["responses"] = {
        "202": {
            "content": {
                "application/json": {
                    "schema": {
                        "allOf": [
                            {"$ref": "#/components/schemas/charge"},
                            {
                                "type": "object",
                                "properties": {"receipt": {"type": "string"}},
                            },
                        ]
                    }
                }
            }
        }
    }

    compiled = compile_spec(spec, tools=["PostCharges"])
    assert compiled.coverage == "1/1"
    assert compiled.pack.resources["charge"].fields["receipt"] == "string"
    assert set(compiled.pack.required_args("PostCharges")) == {"amount", "currency"}


def test_request_argument_types_and_optional_fields_are_preserved() -> None:
    spec = copy.deepcopy(SPEC)
    operation = spec["paths"]["/v1/charges"]["post"]
    body = operation["requestBody"]["content"]["application/x-www-form-urlencoded"]["schema"]
    body["properties"]["description"] = {"type": "string"}
    operation["parameters"] = [
        {"name": "attempt", "in": "header", "required": True, "schema": {"type": "integer"}}
    ]

    pack = compile_spec(spec, tools=["PostCharges"]).pack
    route = pack.routes["PostCharges"]
    assert "description" in route.accepts
    assert route.argument_types["amount"] == "integer"
    assert route.argument_types["attempt"] == "integer"
    assert pack.arguments("PostCharges")["description"] == "string"


def test_non_openapi_3_input_fails_clearly() -> None:
    with pytest.raises(ValueError, match="only OpenAPI 3.x.*Swagger 2.0"):
        compile_spec({"swagger": "2.0", "paths": {}})


# --- a compiled pack actually runs ---------------------------------------


def test_a_compiled_pack_drives_a_working_twin(compiled) -> None:
    twin = Twin(compiled.pack)

    created = twin.call("PostCharges", {"amount": 500, "currency": "usd"})
    assert created["amount"] == 500

    fetched = twin.call("GetChargesCharge", {"charge": created["id"]})
    assert fetched["id"] == created["id"]

    twin.call("PostChargesChargeCapture", {"charge": created["id"]})
    assert twin.call("GetChargesCharge", {"charge": created["id"]})["amount"] == 500
    assert "charge" not in twin.state()["charge"][created["id"]], (
        "the path param name must not leak into the stored record"
    )


# --- the merge that protects hand edits ---------------------------------


def test_hand_edits_win_and_none_deletes() -> None:
    generated = {
        "name": "payments",
        "resources": {"charge": {"id_field": "id", "fields": {"amount": "integer"}}},
        "routes": {
            "GetCharge": {"resource": "charge", "verb": "read"},
            "CaptureCharge": {"resource": "charge", "verb": "update"},
        },
    }
    human = {
        "resources": {
            "charge": {
                "state_field": "status",
                "transitions": {"pending": ["succeeded"], "succeeded": []},
                "fields": {"amount": "number"},  # correct the compiler
            }
        },
        "routes": {
            "GetCharge": {"description": "Fetch a charge."},
            "CaptureCharge": {"sets_state": "succeeded"},
        },
    }

    merged = merge_patch(generated, human)
    pack = Pack.model_validate(merged)

    assert pack.resources["charge"].fields["amount"] == "number", "the human wins"
    assert pack.resources["charge"].transitions == {"pending": ["succeeded"], "succeeded": []}
    assert pack.routes["GetCharge"].description == "Fetch a charge."
    assert pack.routes["GetCharge"].verb == "read", "untouched generated values survive"

    pruned = merge_patch(merged, {"resources": {"charge": {"transitions": None}}})
    assert "transitions" not in pruned["resources"]["charge"], "None deletes"
