"""Témaspecifikus feldolgozási szabályok – tiszta, mellékhatásmentes függvények.

Ebben a modulban nincs adatbázis-, fájl- vagy hálózati művelet. Minden függvény
csak a bemeneteitől függ, és nem módosítja őket, ezért egységtesztekkel közvetlenül,
rögzített adatokon ellenőrizhető. Az időfüggő számítások a ``reference_date``
paramétert használják a "mai nap" helyett, így ugyanaz a bemenet mindig ugyanazt az
eredményt adja (ezt használja a visszaállítás-ellenőrzés is).

Szabály 1 – késedelmi díj
    bemenet:  esedékesség, referencia-dátum, napi díj, díjplafon
    eredmény: fizetendő díj (Ft, egész)
    feltétel: csak a határidő utáni napok számítanak; a díj a plafonnál nem nő tovább
    korlát:   naptári napokkal számol, munkaszüneti napokat nem vesz figyelembe

Szabály 2 – kölcsönzési kérés elbírálása
    bemenet:  a könyv szabad példányszáma, a tag aktív és késedelmes kölcsönzéseinek
              száma, hogy ugyanezt a könyvet már kint tartja-e, valamint a limit
    eredmény: engedélyezett-e, és ha nem, mely okok miatt (minden ok felsorolva)
    feltétel: van szabad példány; a tag nem érte el a limitet; nincs késedelme;
              ugyanazt a címet nem tartja már kint
    korlát:   előjegyzést (várólistát) nem kezel
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class LoanRequestContext:
    """A kölcsönzési döntéshez szükséges, már lekérdezett tények."""

    available_copies: int
    member_active_loans: int
    member_overdue_loans: int
    member_has_same_book: bool
    max_active_loans: int


@dataclass(frozen=True)
class LoanDecision:
    """A döntés eredménye; ``reasons`` üres, ha engedélyezett."""

    allowed: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class LoanView:
    """Egy kölcsönzés díjszámításhoz szükséges, adatbázistól független nézete."""

    loan_id: int
    due_date: date
    returned_at: date | None


def days_overdue(due_date: date, reference_date: date) -> int:
    """Hány naptári nappal lépte túl a referencia-dátum az esedékességet (0, ha nem)."""
    return max(0, (reference_date - due_date).days)


def calculate_late_fee(due_date: date, reference_date: date, daily_fee: int, max_fee: int) -> int:
    """Késedelmi díj: ``napok × napi díj``, de legfeljebb ``max_fee``.

    Hibás paraméter (negatív díj vagy plafon) esetén ValueError.
    """
    if daily_fee < 0 or max_fee < 0:
        raise ValueError("A napi díj és a díjplafon nem lehet negatív")
    return min(days_overdue(due_date, reference_date) * daily_fee, max_fee)


def available_copies(copies_total: int, active_loan_count: int) -> int:
    """Szabad példányok száma; sosem negatív."""
    return max(0, copies_total - active_loan_count)


def evaluate_loan_request(ctx: LoanRequestContext) -> LoanDecision:
    """Kölcsönzési kérés elbírálása; minden sértett feltételt visszaad."""
    reasons: list[str] = []
    if ctx.available_copies <= 0:
        reasons.append("Nincs szabad példány a könyvből")
    if ctx.member_active_loans >= ctx.max_active_loans:
        reasons.append(f"A tag elérte a maximális aktív kölcsönzések számát ({ctx.max_active_loans})")
    if ctx.member_overdue_loans > 0:
        reasons.append("A tagnak késedelmes kölcsönzése van")
    if ctx.member_has_same_book:
        reasons.append("A tag már kint tartja ezt a könyvet")
    return LoanDecision(allowed=not reasons, reasons=tuple(reasons))


def loan_fee_at(loan: LoanView, reference_date: date, daily_fee: int, max_fee: int) -> int:
    """Egy kölcsönzés díja: visszahozott tételnél a visszahozás napjáig, egyébként a referencia-napig."""
    end = loan.returned_at if loan.returned_at is not None else reference_date
    return calculate_late_fee(loan.due_date, end, daily_fee, max_fee)


def overdue_summary(loans: Iterable[LoanView], reference_date: date, daily_fee: int, max_fee: int) -> dict[int, int]:
    """Kölcsönzés-azonosító → díj leképezés a késedelmes (díjköteles) tételekre.

    Determinisztikus eredmény; a visszaállítás-ellenőrzés a forrás és a cél
    adatbázison ugyanezzel a referencia-dátummal hasonlítja össze.
    """
    result = {loan.loan_id: loan_fee_at(loan, reference_date, daily_fee, max_fee) for loan in loans}
    return {loan_id: fee for loan_id, fee in sorted(result.items()) if fee > 0}
