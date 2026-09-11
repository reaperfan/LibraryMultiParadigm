"""Alkalmazási (várható) hibák közös alaposztálya; a HTTP-leképezést a main.py végzi."""


class AppError(Exception):
    """Várható, kezelt hiba – nem programhiba, ezért nem kell hívási lánccal naplózni."""


class NotFoundError(AppError):
    """A kért rekord nem létezik."""


class ConflictError(AppError):
    """Az adat ütközik egy meglévővel (pl. ISBN, e-mail)."""


class RuleViolationError(AppError):
    """A témaspecifikus szabály nem engedi a műveletet; ``reasons`` sorolja fel az okokat."""

    def __init__(self, reasons: tuple[str, ...]) -> None:
        super().__init__("; ".join(reasons))
        self.reasons = reasons
