"""Egységtesztek a témaspecifikus szabályhoz (tiszta függvények, rögzített adatok).

Minden elvárt érték a szabály definíciójából van levezetve, nem a függvény
újrafuttatásából: díj = max(0, referencia − esedékesség) × napi díj, plafonnal.
"""

from datetime import date

import pytest

from backend.services import rules

DUE = date(2026, 9, 3)


@pytest.mark.parametrize(
    ("reference", "daily", "cap", "expected"),
    [
        (date(2026, 9, 3), 50, 2000, 0),  # határeset: az esedékesség napján még nincs díj
        (date(2026, 9, 4), 50, 2000, 50),  # 1 nap késés
        (date(2026, 9, 11), 50, 2000, 400),  # normál: 8 nap × 50
        (date(2026, 8, 30), 50, 2000, 0),  # határidő előtt visszahozva
        (date(2026, 12, 1), 50, 2000, 2000),  # 89 nap × 50 = 4450 → plafon 2000
        (date(2026, 10, 13), 50, 2000, 2000),  # pontosan 40 nap × 50 = 2000 = plafon
        (date(2026, 9, 11), 0, 2000, 0),  # nulla napi díj
    ],
)
def test_calculate_late_fee(reference: date, daily: int, cap: int, expected: int) -> None:
    assert rules.calculate_late_fee(DUE, reference, daily, cap) == expected


@pytest.mark.parametrize(("daily", "cap"), [(-1, 2000), (50, -5)])
def test_calculate_late_fee_rejects_negative_parameters(daily: int, cap: int) -> None:
    with pytest.raises(ValueError):
        rules.calculate_late_fee(DUE, date(2026, 9, 11), daily, cap)


def _ctx(**overrides) -> rules.LoanRequestContext:
    base = dict(
        available_copies=1,
        member_active_loans=0,
        member_overdue_loans=0,
        member_has_same_book=False,
        max_active_loans=3,
    )
    return rules.LoanRequestContext(**{**base, **overrides})


@pytest.mark.parametrize(
    ("ctx", "allowed", "reason_count"),
    [
        (_ctx(), True, 0),  # normál eset
        (_ctx(member_active_loans=2), True, 0),  # határeset: limit alatt eggyel
        (_ctx(member_active_loans=3), False, 1),  # határeset: limit elérve
        (_ctx(available_copies=0), False, 1),  # nincs példány
        (_ctx(member_overdue_loans=1), False, 1),  # késedelem
        (_ctx(member_has_same_book=True), False, 1),  # ugyanaz a könyv kint
        (_ctx(available_copies=0, member_active_loans=3, member_overdue_loans=2, member_has_same_book=True), False, 4),
    ],
)
def test_evaluate_loan_request(ctx: rules.LoanRequestContext, allowed: bool, reason_count: int) -> None:
    decision = rules.evaluate_loan_request(ctx)
    assert decision.allowed is allowed
    assert len(decision.reasons) == reason_count


def test_evaluate_does_not_mutate_input() -> None:
    ctx = _ctx(available_copies=0)
    before = ctx
    rules.evaluate_loan_request(ctx)
    assert ctx == before  # frozen dataclass, azonos tartalom


def test_overdue_summary_uses_return_date_for_closed_loans() -> None:
    loans = [
        rules.LoanView(1, date(2026, 8, 15), date(2026, 8, 14)),  # időben visszahozva → nincs a listában
        rules.LoanView(2, date(2026, 9, 3), None),  # aktív, 8 nap késés → 400
        rules.LoanView(3, date(2026, 7, 15), date(2026, 8, 30)),  # lezárt, 46 nap × 50 = 2300 → plafon 2000
    ]
    result = rules.overdue_summary(loans, date(2026, 9, 11), daily_fee=50, max_fee=2000)
    assert result == {2: 400, 3: 2000}
    assert rules.overdue_summary(loans, date(2026, 9, 11), 50, 2000) == result  # determinisztikus
