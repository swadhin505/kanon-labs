"""The agents under test for this domain.

Two versions of the same support agent: `good`, and `broken` after a prompt
change that dropped "always look the member up first" and "never approve past
the annual cap". Staging the regression this way is what makes the CI demo real
rather than a mock-up.

They are fixed scripts because there is no LLM agent yet -- and because a
deterministic agent proves the *environment* contributes no variance. When a
real agent lands it implements the same two methods and nothing else changes.

Note `CLM-0002`: the id of a claim that does not exist yet is knowable in
advance, because the twin counts ids instead of generating them.
"""

from pathlib import Path

from kanon.gate.runner import Call, Say, ScriptedAgent
from kanon.sut import LLMAgent
from kanon.twin import Pack

HERE = Path(__file__).parent
PACK = Pack.from_yaml(HERE / "pack.yaml")


def _prompt(name: str) -> str:
    return (HERE / "prompts" / f"{name}.md").read_text(encoding="utf-8")


GOOD = ScriptedAgent(
    "good",
    {
        "hi-001": [
            Call("get_member", {"member_id": "MEM-0001"}),
            Call("submit_claim", {"member_id": "MEM-0001", "service_code": "D2740", "amount": 800}),
            Call("review_claim", {"claim_id": "CLM-0002"}),
            Call("approve_claim", {"claim_id": "CLM-0002", "approved_amount": 720}),
            Say("Your claim is approved for 720."),
        ],
        "hi-002": [
            Call("get_member", {"member_id": "MEM-0001"}),
            Call("get_plan", {"plan_id": "PLN-0002"}),
            Call(
                "submit_claim",
                {"member_id": "MEM-0001", "service_code": "D6010", "amount": 40_000},
            ),
            Call("review_claim", {"claim_id": "CLM-0002"}),
            # 25000 cap, 162 already paid out this year.
            Call("approve_claim", {"claim_id": "CLM-0002", "approved_amount": 24_838}),
            Say("Approved up to your remaining Gold annual limit."),
        ],
        "hi-003": [
            Call("get_member", {"member_id": "MEM-0002"}),
            Say("Your coverage has lapsed, so I cannot file this claim."),
        ],
        "hi-004": [
            Call("list_claims", {"member_id": "MEM-0001"}),
            Say("You have one claim, CLM-0001, already paid."),
        ],
    },
)

# The regression. Only the adversarial cap slice and the P1 lookup slices move;
# the read-only slice is untouched -- which is exactly why the aggregate barely
# budges and the per-slice table is the only place you can see it.
BROKEN = ScriptedAgent(
    "broken",
    {
        "hi-001": [
            Call("submit_claim", {"member_id": "MEM-0001", "service_code": "D2740", "amount": 800}),
            Call("review_claim", {"claim_id": "CLM-0002"}),
            Call("approve_claim", {"claim_id": "CLM-0002", "approved_amount": 720}),
        ],
        "hi-002": [
            Call("get_member", {"member_id": "MEM-0001"}),
            Call("get_plan", {"plan_id": "PLN-0002"}),
            Call(
                "submit_claim",
                {"member_id": "MEM-0001", "service_code": "D6010", "amount": 40_000},
            ),
            Call("review_claim", {"claim_id": "CLM-0002"}),
            Call("approve_claim", {"claim_id": "CLM-0002", "approved_amount": 40_000}),
        ],
        "hi-003": [
            Call("get_member", {"member_id": "MEM-0002"}),
            Call("submit_claim", {"member_id": "MEM-0002", "service_code": "D0120", "amount": 100}),
        ],
        "hi-004": [Call("list_claims", {"member_id": "MEM-0001"})],
    },
)

# The realistic regression, and the one the product exists for: a prompt tweak
# that only misfires when the user pushes back. Everything else still works, so
# the headline number barely moves and only the per-slice table shows it.
SUBTLE = ScriptedAgent(
    "subtle",
    {
        **GOOD.scripts,
        "hi-002": [
            Call("get_member", {"member_id": "MEM-0001"}),
            Call("get_plan", {"plan_id": "PLN-0002"}),
            Call(
                "submit_claim",
                {"member_id": "MEM-0001", "service_code": "D6010", "amount": 40_000},
            ),
            Call("review_claim", {"claim_id": "CLM-0002"}),
            Call("approve_claim", {"claim_id": "CLM-0002", "approved_amount": 40_000}),
            Say("Approved in full."),
        ],
    },
)

# The real agents under test. Same two methods as the scripted ones above, so
# nothing in the gate changes -- but these vary between trials, which is what
# finally makes pass^k mean something.
#
# The regression is staged by editing a prompt file, not Python: `support.md`
# knows about the annual cap, `support-no-cap.md` does not. That is the actual
# customer workflow.
LLM = LLMAgent("llm", PACK, _prompt("support"))
LLM_NO_CAP = LLMAgent("llm-no-cap", PACK, _prompt("support-no-cap"))

AGENTS = {
    "good": GOOD,
    "broken": BROKEN,
    "subtle": SUBTLE,
    "llm": LLM,
    "llm-no-cap": LLM_NO_CAP,
}
