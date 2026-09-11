"""Karbantartási mód (íráskorlátozás) futásidejű állapota.

Két forrásból áll össze:
* ``MAINTENANCE_MODE`` környezeti változó – a karbantartó a szolgáltatói API-n
  állítja, ezért egy újratelepítés után is érvényben marad;
* futásidejű kapcsoló – az ``/admin/maintenance`` végponton azonnal, újraindítás
  nélkül is bekapcsolható.

Amíg bármelyik igaz, az alkalmazási írások (POST/PUT/PATCH/DELETE a nem admin
útvonalakon) 503-as választ kapnak.
"""

import logging
import threading

logger = logging.getLogger(__name__)


class MaintenanceFlag:
    """Szálbiztos kapcsoló."""

    def __init__(self, initial: bool = False) -> None:
        self._enabled = initial
        self._lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        with self._lock:
            return self._enabled

    def set(self, value: bool) -> None:
        with self._lock:
            changed = self._enabled != value
            self._enabled = value
        if changed:
            logger.warning("Karbantartási mód %s", "BEKAPCSOLVA" if value else "kikapcsolva")


maintenance_flag = MaintenanceFlag()
