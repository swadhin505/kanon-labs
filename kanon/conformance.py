"""Property-based request and response conformance for a served twin."""

from __future__ import annotations

from dataclasses import dataclass

from kanon.serve import create_http_app
from kanon.twin import Pack


@dataclass(frozen=True)
class FuzzReport:
    operations: int
    cases: int


class FuzzFailure(RuntimeError):
    pass


def fuzz(pack: Pack, examples: int = 25, seed: int = 1) -> FuzzReport:
    """Fuzz every operation with valid and invalid schema-generated requests."""
    if examples < 1:
        raise ValueError("examples must be at least 1")
    try:
        import schemathesis
        from hypothesis import HealthCheck, given, settings
        from hypothesis import seed as hypothesis_seed
        from hypothesis.errors import Unsatisfiable
    except ImportError as exc:  # pragma: no cover - optional extra
        raise RuntimeError("fuzzing needs `pip install -e .[fuzz]`") from exc

    app = create_http_app(pack)
    schema = schemathesis.openapi.from_asgi("/openapi.json", app)
    operations = [result.ok() for result in schema.get_all_operations()]
    cases = 0

    for operation_index, operation in enumerate(operations):
        for mode_index, mode in enumerate(schemathesis.GenerationMode):
            strategy = operation.as_strategy(generation_mode=mode)

            @hypothesis_seed(seed + operation_index * 2 + mode_index)
            @settings(
                max_examples=examples,
                deadline=None,
                database=None,
                suppress_health_check=[HealthCheck.too_slow],
            )
            @given(case=strategy)
            def check(case):
                nonlocal cases
                app.state.twin.reset()
                cases += 1
                case.call_and_validate()

            try:
                check()
            except Unsatisfiable:
                continue
            except Exception as exc:
                raise FuzzFailure(f"{operation.label} ({mode.value}): {exc}") from exc

    return FuzzReport(len(operations), cases)
