# Mentések

Ide kerülnek a karbantartó JSON-mentései: `backup_<UTC-időbélyeg>_<forrás-dpg-id>.json`.
A mentések a forrástól és a backendtől független, tartós helyen maradnak (ez a könyvtár
a vezérlő gépén; a bemutató mentés titkoktól és személyes adatoktól mentes másolata a
`docs/` alatt található). Formátum: `{"manifest": {...}, "data": {tábla: [sor, ...]}}`.
