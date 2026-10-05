"""Rebuild local geographic data from frontend's pinned country-state-city package.

Run from any directory after npm install: python backend/scripts/build_location_data.py
Only the small country list goes into the browser. Cities stay in indexed SQLite.
"""
import json
import shutil
import sqlite3
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "frontend/node_modules/country-state-city/lib/assets"
TARGET = ROOT / "backend/app/data/locations"


def folded(value):
    return "".join(c for c in unicodedata.normalize("NFKD", value.casefold()) if not unicodedata.combining(c))


def main():
    TARGET.mkdir(parents=True, exist_ok=True)
    countries = json.loads((SOURCE / "country.json").read_text(encoding="utf-8"))
    states = json.loads((SOURCE / "state.json").read_text(encoding="utf-8"))
    cities = json.loads((SOURCE / "city.json").read_text(encoding="utf-8"))
    compact = [{"code": c["isoCode"], "name": c["name"]} for c in countries]
    for path in [TARGET / "countries.json", ROOT / "frontend/src/lib/countries.json"]:
        path.write_text(json.dumps(compact, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    database = TARGET / "locations.sqlite3"
    if database.exists():
        database.unlink()
    with sqlite3.connect(database) as db:
        db.executescript("""
            CREATE TABLE states(country TEXT, code TEXT, name TEXT, key TEXT, PRIMARY KEY(country, code));
            CREATE TABLE cities(id INTEGER PRIMARY KEY, country TEXT, state TEXT, name TEXT, key TEXT);
            CREATE INDEX city_country_name ON cities(country, key);
            CREATE INDEX city_state_name ON cities(country, state, key);
        """)
        db.executemany("INSERT INTO states VALUES (?, ?, ?, ?)",
            [(s["countryCode"], s["isoCode"], s["name"], folded(s["name"])) for s in states])
        db.executemany("INSERT INTO cities(country,state,name,key) VALUES (?, ?, ?, ?)",
            [(c[1], c[2], c[0], folded(c[0])) for c in cities])
    shutil.copyfile(SOURCE.parents[1] / "LICENSE", TARGET / "LICENSE")
    public_license = ROOT / "frontend/public/licenses/country-state-city.txt"
    public_license.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SOURCE.parents[1] / "LICENSE", public_license)
    print(f"Generated {len(countries)} countries, {len(states)} states and {len(cities)} cities.")


if __name__ == "__main__":
    main()
