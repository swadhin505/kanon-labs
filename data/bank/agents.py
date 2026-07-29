"""The agents under test for the bank domain."""

from kanon.gate.runner import Call, Say, ScriptedAgent

TELLER = ScriptedAgent(
    "teller",
    {
        "bk-001": [
            Call("get_account", {"account_id": "ACC-0001"}),
            Call(
                "request_transfer",
                {"from_account": "ACC-0001", "to_account": "ACC-0002", "amount": 200},
            ),
            Call("post_transfer", {"transfer_id": "TRF-0001"}),
            Say("Sent 200 to ACC-0002."),
        ],
        "bk-002": [
            Call("get_account", {"account_id": "ACC-0002"}),
            Call(
                "request_transfer",
                {"from_account": "ACC-0002", "to_account": "ACC-0001", "amount": 5000},
            ),
            Call("post_transfer", {"transfer_id": "TRF-0001"}),  # refused: insufficient funds
            Call("reject_transfer", {"transfer_id": "TRF-0001", "reason": "insufficient funds"}),
            Say("You only have 50 available, so I could not send that."),
        ],
    },
)

AGENTS = {"teller": TELLER}
