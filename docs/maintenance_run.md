# Felhős cserepróba – napló és eredmény

Egyetlen, teljes, dokumentált cserepróba (6.5. szakasz). A futás naplója titkoktól
megtisztítva: [`docs/logs/maintenance_run_2026-09-11.log`](logs/maintenance_run_2026-09-11.log)
(a program a kapcsolati címeket eleve `user:***@host` alakban naplózza; az API-kulcs sehol nem
szerepel). A naplóban az időbélyegek helyi időben (CEST, UTC+2) vannak, a karbantartási időpont UTC-ben.

## Környezet

| Elem | Érték |
|---|---|
| Dátum | 2026-09-11, 14:15–14:34 UTC |
| Render workspace (owner) | `tea-dai09967bikc73e149dg` |
| Backend szolgáltatás | `srv-dai0c81594qs738sgpug` (https://librarymultiparadigm.onrender.com) |
| Forrás adatbázis (régi) | `dpg-dai0abp42hec73aq7hrg-a` (free, frankfurt, PostgreSQL 18, lejárat 2026-10-11) |
| Új adatbázis | `dpg-dai0roid0e5s7399mdhg-a` (free, frankfurt, PostgreSQL 18) |
| Vezérlő futási helye | helyi gép (Windows 11), `python -m maintenance.controller run --approve-source dpg-dai0abp42hec73aq7hrg-a --allow-delete dpg-dai0abp42hec73aq7hrg-a` |
| Próba-adatbázis (helyi) | `sqlite:///./verify_local.db` (vállalt korlát: SQLite, nem PostgreSQL) |
| Csere oka | `MAINT_MAINTENANCE_AT=2026-09-11T14:18:01+00:00` konfigurált karbantartási időpont |
| Ellenőrzési időköz | `MAINT_CHECK_INTERVAL_MINUTES=2` |
| Engedélyek | `--approve-source` és `--allow-delete` a forrás azonosítójára (CLI) |
| Mentés | `backups/backup_20260911T141920Z_dpg-dai0abp42hec73aq7hrg-a.json` (books 8, members 4, loans 7, SHA-256 a manifesztben) |

A csere előtt a felületről/API-n egy új kölcsönzés (#7) került rögzítésre, hogy a mentés a kezdőadatokon
felüli rekordot is tartalmazza.

## Időzített, aszinkron ellenőrzés (részlet a naplóból)

```
16:15:06 INFO [maintenance] Ütemező elindult: 2 percenként ellenőrzés
16:15:06 INFO [maintenance] Ellenőrzés: példány=dpg-dai0abp… állapot=available lejárat=2026-10-11T13:42:07+00:00 backend={'status': 'ok', 'maintenance': False} -> csere=False (Nincs csereok)
16:17:06 INFO [maintenance] Ellenőrzés: … -> csere=False (Nincs csereok)
16:19:06 INFO [maintenance] Ellenőrzés: … -> csere=True (Tervezett karbantartás esedékes (2026-09-11T14:18:01+00:00))
16:19:06 INFO [maintenance.rotation] === [20260911141906-6eb080] lépés: preflight
```

Az ellenőrzés `httpx.AsyncClient`-tel (`RenderClient.get_postgres`, `BackendClient.health`) fut,
APScheduler `AsyncIOScheduler` ütemezi; a döntést a tiszta `decisions.decide_rotation` hozza.

## A csere lépései (a napló alapján)

| # | Lépés | Idő (helyi) | Eredmény |
|---|---|---|---|
| 1 | preflight | 16:19:06 | forrás/backend/workspace egyezik, kvóta és törlési engedély rendben; új példány: free / frankfurt / pg18 |
| 2 | freeze_writes | 16:19:08 | `MAINTENANCE_MODE=true` (Render env) + `/admin/maintenance`; 10 mp várakozás; alkalmazási próbaírás → 503 |
| 3 | backup | 16:19:18 | JSON-mentés a forrás külső címéről, manifeszt ellenőrizve |
| 4 | verify_local_restore | 16:19:20 | helyi visszaállítás + ellenőrzés: minden `True` |
| 5 | delete_old | 16:19:20 | `dpg-dai0abp…` törölve; törlés befejezése ellenőrizve (GET → 404) |
| 6 | create_new | 16:19:21 | `dpg-dai0roid…` létrehozva API-val; `creating` → `available` 53 mp után |
| 7 | restore_new | 16:20:16 | **első kísérlet sikertelen** – lásd lent; folytatás után 16:32:10 visszaállítás kész, ellenőrzés `True` |
| 8 | switch_backend | 16:32:12 | `DATABASE_URL` frissítve, deploy `dep-dai11pad0e5s739ah3tg`: build → update → `live` (16:33:44) |
| 9 | postcheck | 16:33:44 | backend karbantartási módban; `/loans` = 7 rekord, díjak egyeznek a mentésből számolttal; próbaírás id=2; app-írás 503 |
| 10 | finalize | 16:33:45 | `state/active_instance.json` = `dpg-dai0roid…`; `MAINTENANCE_MODE=false`, karbantartás feloldva |

## Részleges hiba és biztonságos folytatás (6.4. szakasz)

A `restore_new` lépés első kísérlete (16:20:18) `OperationalError: SSL connection has been closed
unexpectedly` hibával leállt. A vezérlő a futásállapotot (`state/current_run.json`, 6 kész lépés, az új
példány rögzített azonosítójával) és a mentést megőrizte, a karbantartási mód érvényben maradt, a
zárfájlt feloldotta.

**Ok:** a Render API-val létrehozott PostgreSQL-példány `ipAllowList` mezője üres (a dashboardon
alapértelmezés a `0.0.0.0/0`), így a példány `available` állapotban sem fogadott külső kapcsolatot.

**Javítás:** `RenderClient.create_postgres` mostantól az `ipAllowList`-et is megadja, és a
`restore_new` lépés elején `ensure_ip_allow_list` PATCH-csel pótolja, ha üres; emellett
`wait_for_database` időkorlátos `SELECT 1` várakozás előzi meg a visszaállítást.

**Folytatás:** `python -m maintenance.controller resume` (16:31:57) ugyanazt a futást a `restore_new`
lépéstől folytatta; **nem jött létre második példány** (a rögzített azonosítót használta), és
**nem duplikálódott adat** (a visszaállítás üres célt követel). A futás a 8–10. lépéssel sikeresen zárult.

## Ellenőrzött eredmény

* `GET /health` → `{"status":"ok","maintenance":false}`
* `GET /stats?reference_date=2026-09-11` az új példányról: `loans_total=7, loans_overdue=3, total_late_fee=1450` –
  azonos a csere előttivel.
* Csere utáni új kölcsönzés (`POST /loans`, Abigél → Tóth Dániel) → `id=8`, azonosítóütközés nélkül.
* A telepített Streamlit-felület (https://libmultiparad.streamlit.app) az új példány adatait mutatja.
* Ismételt `check` az új példányra: `csere=False` – a már teljesített karbantartási időpont
  (`active_instance.json`) nem indít újabb cserét.

## Bemutató mentés

A cserepróba mentése fiktív mintaadatokat tartalmaz, titkot nem; a leadott másolat:
[`docs/sample_backup.json`](sample_backup.json) (azonos formátum, a kezdőadatokkal).
