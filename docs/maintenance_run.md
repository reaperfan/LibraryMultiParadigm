# Felhős cserepróba – napló és eredmény

> **Kitöltendő a tényleges felhős futás után.** Az alábbi sablon a 6.5. szakasz
> szerinti egyetlen, teljes, dokumentált cserepróba rögzítésére szolgál. A naplót a
> `state/maintenance.log` fájlból kell ide másolni **titkoktól megtisztítva**
> (API-kulcs, jelszó, kapcsolati cím jelszórésze kitakarva – a program a
> kapcsolati címeket eleve `user:***@host` alakban naplózza).

## Környezet

| Elem | Érték |
|---|---|
| Dátum (UTC) | _2026-..-.._ |
| Render workspace (owner) | `tea-…` |
| Backend szolgáltatás | `srv-…` (https://….onrender.com) |
| Forrás adatbázis (régi) | `dpg-…` (free, frankfurt, PostgreSQL 16) |
| Új adatbázis | `dpg-…` (free, frankfurt, PostgreSQL 16) |
| Vezérlő futási helye | helyi gép (Windows 11), `python -m maintenance.controller run` |
| Próba-adatbázis (helyi) | `sqlite:///./verify_local.db` (vállalt korlát: SQLite, nem PostgreSQL) |
| Csere oka | `MAINT_MAINTENANCE_AT=…` konfigurált karbantartási időpont |
| Engedélyek | `MAINT_APPROVED_SOURCE_ID=dpg-…`, `MAINT_ALLOW_DELETE_SOURCE_ID=dpg-…` |

## Időzített, aszinkron ellenőrzés (részlet a naplóból)

```
… INFO [maintenance] Ütemező elindult: 15 percenként ellenőrzés
… INFO [maintenance] Ellenőrzés: példány=dpg-… állapot=available lejárat=2026-…T…+00:00 backend={'status': 'ok', 'maintenance': False} -> csere=False (Nincs csereok)
… INFO [maintenance] Ellenőrzés: példány=dpg-… állapot=available lejárat=… -> csere=True (Tervezett karbantartás esedékes (…))
```

## A csere lépései (a napló alapján)

| # | Lépés | Kezdés | Eredmény |
|---|---|---|---|
| 1 | preflight | | forrás/backend/workspace azonosítók, kvóta és próba-DB rendben |
| 2 | freeze_writes | | `MAINTENANCE_MODE=true` (env) + `/admin/maintenance`; próbaírás → 503 |
| 3 | backup | | `backups/backup_…Z_dpg-….json`, sorok: books=…, members=…, loans=… |
| 4 | verify_local_restore | | helyi Postgres: minden ellenőrzés `True` |
| 5 | delete_old | | `dpg-…` törölve, törlés befejezése ellenőrizve (404) |
| 6 | create_new | | `dpg-…` létrehozva, `available` … mp után |
| 7 | restore_new | | visszaállítás + ellenőrzés az új példányon: `True` |
| 8 | switch_backend | | `DATABASE_URL` frissítve, deploy `dep-…` → `live` |
| 9 | postcheck | | `/loans` = mentés (… rekord, díjak egyeznek), próbaírás id=…, app-írás 503 |
| 10 | finalize | | `state/active_instance.json` = `dpg-…`, karbantartás feloldva |

## Ellenőrzött eredmény

* A telepített frontend az új példányról listázza a könyveket és a kölcsönzéseket.
* A `/stats?reference_date=2026-09-11` válasz megegyezik a csere előttivel
  (`loans_overdue`, `total_late_fee`).
* Csere utáni új kölcsönzés rögzítése sikeres, azonosítóütközés nélkül.

## Titkoktól megtisztított teljes napló

Lásd: [`docs/logs/maintenance_run_<dátum>.log`](logs/) _(a futás után ide másolandó)_.

## Bemutató mentés

A cserepróba mentésének titkoktól és személyes adatoktól mentes másolata:
[`docs/sample_backup.json`](sample_backup.json) _(fiktív mintaadatok)_.
