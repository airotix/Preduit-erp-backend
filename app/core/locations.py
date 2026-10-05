"""Local geographic lookups: no external requests or business-database queries."""
import json
import sqlite3
import unicodedata
from contextlib import closing
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "data/locations"


def folded(value: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", value.strip().casefold())
                   if not unicodedata.combining(c))


@lru_cache(maxsize=1)
def countries() -> tuple[dict, ...]:
    return tuple(json.loads((DATA / "countries.json").read_text(encoding="utf-8")))


def country_code(value: str | None) -> str | None:
    key = folded(value or "")
    aliases = {"uk": "GB", "usa": "US", "uae": "AE", "united states of america": "US"}
    if key in aliases:
        return aliases[key]
    return next((c["code"] for c in countries()
                 if key in (c["code"].lower(), folded(c["name"]))), None)


def country_name(value: str | None) -> str:
    code = country_code(value)
    return next((c["name"] for c in countries() if c["code"] == code), value or "")


def connection():
    return closing(sqlite3.connect((DATA / "locations.sqlite3").as_uri() + "?mode=ro&immutable=1", uri=True))


@lru_cache(maxsize=256)
def states(country: str) -> tuple[dict, ...]:
    with connection() as db:
        rows = db.execute("SELECT code,name FROM states WHERE country=? ORDER BY key", (country,)).fetchall()
    return tuple({"code": code, "name": name} for code, name in rows)


def state_code(country: str, value: str | None) -> str | None:
    key = folded(value or "")
    return next((s["code"] for s in states(country)
                 if key in (s["code"].lower(), folded(s["name"]))), None)


@lru_cache(maxsize=512)
def search_cities(country: str, state: str | None, query: str) -> tuple[dict, ...]:
    key = folded(query)
    sql = "SELECT c.id,c.name,c.state,s.name FROM cities c LEFT JOIN states s ON s.country=c.country AND s.code=c.state WHERE c.country=?"
    params = [country]
    if state:
        sql += " AND c.state=?"
        params.append(state)
    if key:
        sql += " AND c.key>=? AND c.key<?"
        params.extend([key, key + "\uffff"])
    sql += " ORDER BY c.key,c.id LIMIT 50"
    with connection() as db:
        rows = db.execute(sql, params).fetchall()
    return tuple({"code": str(i), "name": name, "stateCode": st, "stateName": sn or ""}
                 for i, name, st, sn in rows)


def clean_location(country=None, state=None, city=None) -> dict:
    """Accept ISO codes or legacy names; persist canonical display names."""
    country, state, city = [(v or "").strip() for v in (country, state, city)]
    if not (country or "").strip():
        if (state or "").strip() or (city or "").strip():
            raise ValueError("Select a country before choosing a province or city.")
        return {"country": None, "state": None, "city": None}
    code = country_code(country)
    if not code:
        raise ValueError("Select a recognized country.")
    st = state_code(code, state) if state else None
    if state and not st:
        raise ValueError("Select a province belonging to the selected country.")
    city_name = None
    if (city or "").strip():
        sql = "SELECT name FROM cities WHERE country=? AND key=?"
        params = [code, folded(city)]
        if st:
            sql += " AND state=?"
            params.append(st)
        with connection() as db:
            row = db.execute(sql + " LIMIT 1", params).fetchone()
        if not row:
            raise ValueError("Select a city belonging to the selected country/province.")
        city_name = row[0]
    return {"country": next(c["name"] for c in countries() if c["code"] == code),
            "state": next((s["name"] for s in states(code) if s["code"] == st), None), "city": city_name}
