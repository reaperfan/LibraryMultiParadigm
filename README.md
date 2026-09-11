# Könyvtári kölcsönzés – többkomponensű Python-alkalmazás

Python-beadandó, *Multi paradigmás programozási nyelvek gyakorlat*, EKKE Informatikai Kar, 2026/2027. I. félév.

| Komponens | Technológia | Felhős elérés |
|---|---|---|
| Backend (REST API) | FastAPI + SQLAlchemy ORM | Render: https://librarymultiparadigm.onrender.com ([/docs](https://librarymultiparadigm.onrender.com/docs)) |
| Frontend | Streamlit | Streamlit Community Cloud: https://libmultiparad.streamlit.app |
| Adatbázis | PostgreSQL (Render), helyben SQLite | Render PostgreSQL, szolgáltatói API-val cserélt példány |
| Karbantartó | `maintenance` csomag (asyncio + httpx + APScheduler) | helyi gépen fut |
| CI | GitHub Actions (`.github/workflows/ci.yml`) | push / pull_request |

---

## 1. Az alkalmazás

### Cél és fő funkciók

Egy kis könyvtár kölcsönzési nyilvántartása: könyvek (több példánnyal), tagok és kölcsönzések
kezelése. Funkciók: katalógus szűrése, kölcsönzés rögzítése szabály alapján, visszavétel
késedelmi díjjal, statisztika (késedelmes tételek, szerzőnkénti kölcsönzések), könyv- és tagfelvitel.

### Témaspecifikus feldolgozási szabály

A szabály a [`backend/services/rules.py`](backend/services/rules.py) modulban van, tiszta függvényként.

**Kölcsönzési kérés elbírálása** – `evaluate_loan_request(ctx)`:

| | |
|---|---|
| Bemenet | `LoanRequestContext`: szabad példányszám, a tag aktív és késedelmes kölcsönzéseinek száma, kint tartja-e már ugyanezt a könyvet, limit |
| Eredmény | `LoanDecision(allowed, reasons)` – **minden** sértett feltétel felsorolva |
| Feltételek | van szabad példány; aktív kölcsönzések < `MAX_ACTIVE_LOANS` (3); nincs késedelmes tétel; ugyanaz a cím nincs kint |
| Korlát | előjegyzést/várólistát nem kezel |

**Késedelmi díj** – `calculate_late_fee(due_date, reference_date, daily_fee, max_fee)`:
`max(0, referencia − esedékesség) × napi díj`, legfeljebb a plafon (alapértelmezés 50 Ft/nap, 2000 Ft).
Naptári napokkal számol. A "mai nap" helyett minden számítás **referencia-dátumot** kap
(`?reference_date=` lekérdezési paraméter, alapértelmezés: ma), ezért ugyanaz a bemenet mindig ugyanazt
adja – ezt használja a visszaállítás-ellenőrzés is.

Elvárt eredmények a mintaadatokon (2026-09-11 referencia-nappal): #2: 8 nap → 400 Ft, #3: 18 nap → 900 Ft,
#4: 3 nap → 150 Ft, #6 (lezárt, 46 nap) → plafon 2000 Ft; a tesztek ezeket rögzítik
([`tests/test_rules.py`](tests/test_rules.py), [`tests/test_backup_restore.py`](tests/test_backup_restore.py)).

### Adatforrás

Fiktív mintaadatok a [`data/`](data/) CSV-fájlokban (a könyvcímek valós magyar regények, a tagok és
kölcsönzések kitaláltak). Induláskor a backend üres adatbázisba betölti őket
([`backend/seed.py`](backend/seed.py)); minden további adat a felületről érkezik.

### Komponensek és adatút

```
Streamlit (frontend/app.py) ──HTTP──▶ FastAPI (backend/routers/loans.py)
      ▲                                     │ LoanService.create_loan()      (backend/services/loan_service.py)
      │                                     │   └─ rules.evaluate_loan_request()  (tiszta szabály)
      │                                     ▼
      └──── JSON válasz ◀──────── repository.add_loan() → SQLAlchemy Session → adatbázis
```

Példa: a "Kölcsönzés rögzítése" gomb → `POST /loans` → `LoanService.create_loan` összegyűjti a tényeket
(`_build_context`), lefuttatja a szabályt, engedély esetén `Loan` sort ment; a válasz (`LoanOut`, díjjal)
az API-n át jut vissza a felületre. A frontend **nem** éri el az adatbázist.

### Paradigmák a kódban

| Paradigma | Hol | Miért így |
|---|---|---|
| **Procedurális** | `RotationController.step_*` lépések ([`maintenance/rotation.py`](maintenance/rotation.py)) és `backend/seed.py` | a csere 10 sorrendfüggő lépés; egy-egy függvény egy lépés, az állapot minden lépés után mentve → követhető és folytatható |
| **Funkcionális** | [`backend/services/rules.py`](backend/services/rules.py) (`calculate_late_fee`, `evaluate_loan_request`, `overdue_summary`), [`maintenance/decisions.py`](maintenance/decisions.py) (`decide_rotation`, `classify_http_error`, `next_step`, `reconcile_created_instance`, `validate_manifest`) | tiszta, mellékhatásmentes, frozen dataclass be- és kimenettel; azonos bemenet → azonos eredmény; nincs I/O → közvetlenül, rögzített adatokon tesztelhetők |
| **Objektumorientált** | `LoanService` ([`backend/services/loan_service.py`](backend/services/loan_service.py)), `RotationController`, `RenderClient` ([`maintenance/render_api.py`](maintenance/render_api.py)), `StateStore`, `ApiClient` ([`frontend/api_client.py`](frontend/api_client.py)), `MaintenanceFlag` | a Session/beállítás/referencia-dátum együtt tartása és a munkafolyamat metódusokra bontása; a kliensek időkorlátot és újrapróbálkozást zárnak egységbe |

---

## 2. Használat

### Követelmények

* **Python 3.12** (CI) – helyben 3.14-gyel is fut; a függőségek verziói a `requirements.txt`-ben rögzítve.
* Helyi fejlesztéshez **SQLite** (beépített). Felhőben PostgreSQL: a `psycopg[binary]` illesztő a
  requirements része, külön telepítés nem kell.
* A karbantartó próbavisszaállításához egy **elkülönített adatbázis** (`MAINT_VERIFY_DATABASE_URL`):
  ajánlott PostgreSQL 16 (pl. Docker: `docker run -d --name library-verify -e POSTGRES_USER=library -e POSTGRES_PASSWORD=library -e POSTGRES_DB=library_verify -p 5433:5432 postgres:16`);
  a dokumentált cserepróbában helyi SQLite-fájl volt (lásd 3. szakasz, vállalt korlát).

### Telepítés és beállítás

```bash
python -m venv venv
venv\Scripts\activate          # Windows  |  source venv/bin/activate (Linux/macOS)
python -m pip install -r requirements.txt
copy .env.example .env         # majd a valódi értékek kitöltése
```

Minden környezetfüggő érték a `.env`-ből jön ([`.env.example`](.env.example)); a `.env`, a
`.streamlit/secrets.toml` és a `venv/` nincs a Gitben. A backend beállításai: [`backend/config.py`](backend/config.py),
a karbantartóé (`MAINT_` előtag): [`maintenance/config.py`](maintenance/config.py).

### Adatbázis-inicializálás

Induláskor a backend létrehozza a sémát (`Base.metadata.create_all`) és üres adatbázisba betölti a
`data/` CSV-ket (`SEED_ON_STARTUP=true`). Külön parancs nem szükséges.

### Indítás és leállítás

Teljes rendszer egy paranccsal (backend + frontend + karbantartó *ellenőrzés*; csere/törlés engedély
nélkül nem indul):

```bash
python start.py
```

Leállítás: `Ctrl+C` (minden alfolyamatot leállít). Külön terminálokból:

```bash
uvicorn backend.main:app --reload
```
```bash
streamlit run frontend/app.py
```
```bash
python -m maintenance.controller run
```
```bash
python -m pytest
```

Támogatott környezet: Windows 11 (fejlesztés), Ubuntu (CI). Elérés: backend `http://127.0.0.1:8000/docs`,
frontend `http://localhost:8501`.

### API-végpontok

| Metódus + útvonal | Leírás |
|---|---|
| `GET /books?q=&author=` | könyvek listázása, szűrés, szabad példányszámmal |
| `GET /books/{id}` | részletes lekérés (404, ha nincs) |
| `POST /books` | könyvfelvitel (Pydantic-validáció, 409 ISBN-ütközésnél) |
| `GET /members`, `GET /members/{id}`, `POST /members` | tagok |
| `GET /loans?status=all\|active\|overdue\|returned&reference_date=` | kölcsönzések díjjal |
| `POST /loans/check` | a szabály előnézete (engedélyezett-e, okok) |
| `POST /loans` | kölcsönzés rögzítése (422 + `reasons`, ha a szabály tilt) |
| `POST /loans/{id}/return` | visszavétel, díj a visszahozás napjáig |
| `GET /stats?reference_date=` | statisztika (késedelmes tételek, díjösszeg, szerzőnkénti kölcsönzés) |
| `GET /health` | állapot + karbantartási mód _(infra)_ |
| `GET/POST /admin/maintenance`, `POST /admin/write-probe` | karbantartás, `X-Admin-Token` fejléccel _(infra)_ |

Hibakezelés: validációs hiba 422 (FastAPI beépített), nem található 404, ütközés 409, szabálysértés 422
`reasons` listával, karbantartás alatt írás 503 `Retry-After` fejléccel. A `Session` kérésenként jön
létre; sikeres kérés végén commit, hibánál rollback ([`backend/database.py`](backend/database.py)).
A frontend minden hívása időkorlátos (`BACKEND_TIMEOUT`), a hibákat és az üres eredményt a felület jelzi
([`frontend/api_client.py`](frontend/api_client.py)). Naplózás a `logging` modullal komponensenként
(`backend`, `backend.services.loan_service`, `maintenance.*`); a karbantartó naplója a
`state/maintenance.log` fájlba is kerül.

### Felhős telepítés

**Render (backend)** – Web Service, Python, build: `pip install -r requirements.txt`,
start: `uvicorn backend.main:app --host 0.0.0.0 --port $PORT`. Környezeti változók: `DATABASE_URL`
(a Render PostgreSQL *internal* címe), `ADMIN_TOKEN`, `MAINTENANCE_MODE=false`, `SEED_ON_STARTUP=true`.
A backend a `postgres://` címet automatikusan a `postgresql+psycopg://` illesztőre irányítja.

**Render (adatbázis)** – PostgreSQL, free, frankfurt, 16-os verzió. Az ingyenes példány 30 nap után
lejár és munkaterületenként egy aktív ingyenes példány engedélyezett – ezért a csere "egy példányos"
útvonalon fut (lásd 3.).

**Streamlit Community Cloud (frontend)** – app fájl: `frontend/app.py`; Secrets:
`BACKEND_URL = "https://librarymultiparadigm.onrender.com"`.

**Üzemeltetési korlátok:** a Render ingyenes webszolgáltatása inaktivitáskor leáll, az első kérés
~30–60 mp indulási késleltetéssel jár (a frontend ezt hibaüzenettel jelzi, a "Frissítés" gomb újrapróbál).
A fájlrendszer nem tartós – ezért minden adat a PostgreSQL-ben van; a backend újraindítása/újratelepítése
nem okoz adatvesztést. A karbantartó helyben fut; napi 24 órás rendelkezésre állás nem cél.

---

## 3. Karbantartás (automatizált adatbázis-csere)

Egyetlen moduláris vezérlőprogram: [`maintenance/controller.py`](maintenance/controller.py).

```
python -m maintenance.controller check                     # egyszeri ellenőrzés
python -m maintenance.controller run [--approve-source dpg-… --allow-delete dpg-…]   # időzített
python -m maintenance.controller rotate --approve-source dpg-… --allow-delete dpg-…  # csere most
python -m maintenance.controller resume                    # félbeszakadt futás folytatása
python -m maintenance.controller backup --source-url … --source-id …
python -m maintenance.controller restore --backup backups/….json --target-url … [--reset]
```

* **Időzítés:** `APScheduler` (`AsyncIOScheduler`), `MAINT_CHECK_INTERVAL_MINUTES` percenként;
  `asyncio.run()` a belépési pont. Az ellenőrzés `httpx.AsyncClient`-tel, `async def`/`await`
  szerkezettel kérdezi a Render API-t (`RenderClient.get_postgres`) és a backend `/health` végpontját.
* **Döntés:** `decisions.decide_rotation` – csereok a **konfigurált karbantartási időpont**
  (`MAINT_MAINTENANCE_AT`, UTC-re egységesítve) vagy a **közelgő lejárat** (`expiresAt` ≤ küszöb nap).
  Elérhetetlen szolgáltatás, kapcsolódási hiba vagy üres tábla *nem* ok. A már teljesített karbantartási
  időpont (`state/active_instance.json`) nem indít ismételt cserét.
* **Engedélyezés:** csere csak a kijelölt forrásra adott `--approve-source <dpg-id>` engedéllyel, a régi
  példány törlése csak `--allow-delete <dpg-id>`-vel (ütemezett módban ugyanezek `MAINT_APPROVED_SOURCE_ID`,
  `MAINT_ALLOW_DELETE_SOURCE_ID`). A `start.py` engedély nélkül indítja → csak ellenőriz. Az admin
  végpontok tokennel védettek.
* **Karbantartási mód:** a vezérlő a Render API-n `MAINTENANCE_MODE=true` változót állít (újratelepítést
  is túlél) *és* az `/admin/maintenance` végponton azonnal bekapcsolja; a backend middleware-e minden
  alkalmazási írást 503-mal utasít el, az admin próbaírás kivétel. Feloldás csak sikeres utóellenőrzés és
  tartós rögzítés után.
* **Lépések** (`RotationController`, [`maintenance/rotation.py`](maintenance/rotation.py)):
  `preflight → freeze_writes → backup → verify_local_restore → delete_old → create_new → restore_new →
  switch_backend → postcheck → finalize`. Minden lépés után a futásállapot a `state/current_run.json`
  fájlba kerül; a `state/rotation.lock` biztosítja, hogy egyszerre egy csere fusson.
* **Mentés formátuma és helye:** JSON (`backups/backup_<UTC>_<forrás-id>.json`), manifeszttel (készítés
  ideje, forrás-azonosító, sorszámok, SHA-256). Export–import SQLAlchemy-vel, az azonosítók megőrzésével,
  PostgreSQL-en a szekvenciák `max(id)+1`-re állításával ([`maintenance/backup.py`](maintenance/backup.py)).
  A mentés a vezérlő gépén, a forrástól és a backendtől függetlenül marad meg.
* **Visszaállítás-ellenőrzés** ([`maintenance/verify.py`](maintenance/verify.py)): sorszám és teljes
  tartalom (ellenőrzőösszeg), idegen kulcsok, a késedelmi díj szabály **azonos referencia-nappal**
  (`MAINT_RULE_REFERENCE_DATE`) számolt eredménye a mentésen és a célon, próbabeszúrás (visszagörgetve)
  az azonosítóképzésre.
* **Egy példányos csomag (Render free):** a végső mentést előbb a helyi próba-adatbázisba
  (`MAINT_VERIFY_DATABASE_URL`) állítja vissza és ellenőrzi. **Vállalt korlát:** a cserepróbában a
  próba-adatbázis helyi SQLite volt (`sqlite:///./verify_local.db`), nem PostgreSQL – a tartalom, a
  kapcsolatok, a szabályeredmény és az azonosítóképzés ellenőrzése így is lefut, de a PostgreSQL-specifikus
  szekvencia-beállítást csak a felhős példányon végzett (`restore_new`) visszaállítás ellenőrzi; csak sikeres ellenőrzés és kifejezett törlési
  engedély után törli a régit, megvárja a törlés befejezését (404), majd API-val hozza létre az újat
  (csomag, régió, verzió kifejezetten megadva), időkorlátos állapotlekérdezéssel várja az `available`
  állapotot.
* **Átállás:** `PUT /services/{id}/env-vars/DATABASE_URL` (csak ez a változó módosul), új deploy
  indítása és `live` állapot megvárása; utóellenőrzés: `/loans` tartalma és díjai = mentés, ellenőrzött
  próbaírás (`/admin/write-probe`), alkalmazási írás továbbra is 503; végül `state/active_instance.json`.
* **Részleges hiba:** átmeneti hiba (429/5xx/hálózat) → exponenciális várakozással legfeljebb
  `MAINT_MAX_RETRIES` újrapróbálkozás; jogosultsági (401/403) vagy kvótahiba (402) → leállás, nincs
  automatikus csomagváltás. Időtúllépés a létrehozásnál → `resume` a rögzített azonosító / a futás neve
  alapján egyezteti a szolgáltatónál, hogy létrejött-e a példány (`reconcile_created_instance`);
  feloldhatatlan bizonytalanságnál hibával leáll. Sikertelen visszaállítás/utóellenőrzés → a
  karbantartás érvényben marad, mentés és állapot megmarad. Újrafuttatás nem duplikál: a visszaállítás
  üres célt követel. Ha a forrás már elérhetetlen, csak külön `restore` futtatható korábbi ellenőrzött
  mentésből, annak időpontját jelezve; mentés hiányában a program leáll.
* **Folytatás:** `python -m maintenance.controller resume` – a `current_run.json` következő lépésétől.

**A teljes felhős cserepróba naplója és eredménye:** [`docs/maintenance_run.md`](docs/maintenance_run.md)
(titkoktól megtisztítva), bemutató mentés: [`docs/sample_backup.json`](docs/sample_backup.json).

---

## 4. Ellenőrzések

```bash
python -m pytest
```

55 tesztfüggvény-futás, 5 fájl ([`tests/`](tests/)):

| Fájl | Mit ellenőriz |
|---|---|
| `test_rules.py` | témaspecifikus szabály: normál, határeset (esedékesség napja, plafon, limit), hibás bemenet – `@pytest.mark.parametrize` |
| `test_maintenance_decisions.py` | csere-döntés, átmeneti szolgáltatói hiba (`httpx.MockTransport`, újrapróbálkozás/feladás), hiányzó-sérült mentés, félbeszakadt létrehozás utáni egyeztetés |
| `test_backup_restore.py` | mentés–visszaállítás körkörös próba, ellenőrzés, nem üres cél elutasítása, manipulált cél felismerése |
| `test_rotation_flow.py` | teljes cserefolyamat helyettesített Render API-val: siker, engedély hiánya, megszakadt létrehozás + `resume` második példány nélkül |
| `test_api.py` | **API-integrációs teszt**: `TestClient` → valódi routerek → `LoanService` → SQLAlchemy → elkülönített SQLite; státuszkód, választörzs és adatbázis-állapot; 404/409/422; karbantartási mód |

A tesztadatbázis a `tests/conftest.py`-ban jön létre ideiglenes könyvtárban, minden tesztnél újra;
élő szolgáltatást, telepített adatbázist vagy titkot a tesztek nem használnak, felhős erőforrást nem
módosítanak.

**CI:** [`.github/workflows/ci.yml`](.github/workflows/ci.yml) – push és pull_request esetén Python 3.12,
rögzített függőségek, `python -m pytest`. A szándékos hiba → sikertelen futás → javítás → sikeres futás
dokumentációja (linkekkel): [`docs/ci_bugfix.md`](docs/ci_bugfix.md).

---

## 5. Tervezési döntések

1. **Időfüggő szabály referencia-dátummal.** Probléma: a késedelmi díj a "mai naptól" függ, így a tesztek
   és a visszaállítás-ellenőrzés napról napra más eredményt adnának. Alternatíva: `date.today()` kipatkolása
   a tesztekben. Választás: minden szabályfüggvény és az API `reference_date` paramétert kap (alapértelmezés
   ma). Indok: a szabály tiszta és determinisztikus marad, a mentés és a cél ugyanazzal a nappal
   hasonlítható össze. Korlát: a kliensnek tudnia kell a paraméterről; a felület mindig a mai napot használja.

2. **JSON export–import saját visszaállítóval `pg_dump` helyett.** Probléma: a mentésnek a vezérlő gépéről
   kell futnia, és helyi SQLite-tal is tesztelhetőnek kell lennie. Alternatíva: `pg_dump`/`pg_restore`
   meghívása. Választás: SQLAlchemy-alapú, táblánkénti JSON export, séma-újraépítés, azonosítók megőrzése,
   szekvenciák beállítása, manifeszt ellenőrzőösszeggel. Indok: nem igényel kliens-eszközt a gépen, azonos
   kód fut a teszten és a felhőn, a tartalom ellenőrzése egyszerű. Korlát: csak az ORM-ben definiált
   táblákat menti; nagyon nagy adatnál lassabb, mint a natív dump.

3. **Csere lépésekre bontva, tartós futásállapottal és kifejezett engedélyekkel.** Probléma: az ingyenes
   csomagon egy példány lehet, a régi törlése visszafordíthatatlan, és a hálózati műveletek megszakadhatnak.
   Alternatíva: egy monolit "csere" függvény, automatikus visszaállással. Választás: tíz lépés, minden
   lépés után mentett állapot, zárfájl, `resume`; törlés csak a végső mentés *helyi* visszaállítása és
   ellenőrzése, valamint a konkrét forrásra adott törlési engedély után. Indok: bármely ponton
   biztonságosan leállítható, újrafuttatás nem hoz létre második példányt és nem duplikál adatot.
   Korlát: automatikus visszaállás nincs (dokumentált folytatás van); a törlés és az új példány
   elérhetősége között az alkalmazás csak olvasható.

---

## 6. Források és bővítések

* **AI-eszköz:** Claude (Anthropic) – tervezés, kódvázak, tesztesetek és dokumentáció generálása.
  Ellenőrzés: a generált kód teljes átolvasása, a tesztcsomag futtatása helyben és CI-ben, a szabály elvárt
  értékeinek kézi levezetése a mintaadatokból, a felület és a karbantartó próbafuttatása. Titok (API-kulcs,
  jelszó) AI-eszköznek nem került átadásra.
* **Átvett minták:** FastAPI/SQLAlchemy 2.0 hivatalos dokumentációjának mintái (függőség-alapú Session,
  `DeclarativeBase`, `TestClient`); Render API-dokumentáció (postgres, env-vars, deploys végpontok).
* **Adatok:** saját, fiktív mintaadatok (`data/*.csv`); a könyvcímek közkincs magyar regények.
* **Bővítés:** nem készült (szálkezelés, scraping, e-mail, Docker nincs vállalva).
