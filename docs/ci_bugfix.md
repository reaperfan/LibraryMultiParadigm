# CI-hibafeltárás: sikertelen, majd javított futás

> **Kitöltendő a GitHub Actions-futások után.** A 4.4. szakasz szerint egy meglévő
> teszt által jelzett, szándékosan előidézett hibával kell igazolni a sikertelen
> futást, majd a javítás után a sikeres futást. A hibás változat NEM kerülhet a
> felhőben futó alkalmazásba, ezért külön ágon (`ci-bugfix-demo`) végezzük.

## Javasolt forgatókönyv

1. Ág: `git checkout -b ci-bugfix-demo`
2. Szándékos hiba a `backend/services/rules.py` `calculate_late_fee` függvényében:
   `days_overdue(...) * daily_fee` helyett `(days_overdue(...) + 1) * daily_fee`
   (az esedékesség napján is díjat számolna).
3. Push → a `tests/test_rules.py::test_calculate_late_fee[...-0]` esetek és a
   `tests/test_backup_restore.py::test_backup_restore_roundtrip_and_verification`
   (szabályeredmény) elbuknak → **piros CI-futás**.
4. Javítás visszaállítása, push → **zöld CI-futás**.

## Rögzítendő

| | Sikertelen futás | Javított futás |
|---|---|---|
| Actions-link | https://github.com/reaperfan/LibraryMultiParadigm/actions/runs/… | https://github.com/reaperfan/LibraryMultiParadigm/actions/runs/… |
| Commit | `…` | `…` |

* **Tünet:** `assert rules.calculate_late_fee(DUE, reference, daily, cap) == expected` – `50 != 0` az
  esedékesség napján.
* **Ok:** a díjképlet egy nappal többet számolt (határeset-hiba).
* **Javítás:** a képlet visszaállítása `days_overdue(due_date, reference_date) * daily_fee`-re.
* **Ellenőrzés:** minden teszt sikeres (55 passed); a felhős alkalmazás a hibás változatot nem kapta meg.
