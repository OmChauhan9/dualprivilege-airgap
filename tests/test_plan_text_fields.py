"""The real planner wrote a pay memo as {"lit": ...}; that must parse, nothing looser."""

import pytest
from pydantic import ValidationError

from airgap.core.plan import Plan


def _pay(memo: object) -> dict:
    return {
        "steps": [
            {
                "op": "pay",
                "payee": {"alias": "ledgerlens"},
                "amount": {"lit": 20},
                "memo": memo,
            }
        ]
    }


def test_lit_wrapped_memo_is_unwrapped() -> None:
    plan = Plan.model_validate(_pay({"lit": "LedgerLens subscription renewal"}))
    assert plan.steps[0].memo == "LedgerLens subscription renewal"  # type: ignore[union-attr]


def test_plain_memo_still_works() -> None:
    assert Plan.model_validate(_pay("renewal")).steps[0].memo == "renewal"  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "bad", [{"var": "d", "field": "x"}, {"lit": 5}, {"lit": "a", "extra": 1}, ["a"]]
)
def test_other_memo_shapes_stay_rejected(bad: object) -> None:
    with pytest.raises(ValidationError):
        Plan.model_validate(_pay(bad))


def test_email_subject_and_body_accept_lit() -> None:
    plan = Plan.model_validate(
        {
            "steps": [
                {
                    "op": "send_email",
                    "to": {"lit": "me@example.test"},
                    "subject": {"lit": "hi"},
                    "body": {"lit": "there"},
                }
            ]
        }
    )
    step = plan.steps[0]
    assert (step.subject, step.body) == ("hi", "there")  # type: ignore[union-attr]
