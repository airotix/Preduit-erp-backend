"""Public, bounded geographic lookups usable during signup and setup."""
from fastapi import APIRouter, HTTPException, Query, Response
from app.core import locations

router = APIRouter(prefix="/locations", tags=["locations"])


@router.get("/countries")
def countries(response: Response):
    response.headers["Cache-Control"] = "public, max-age=86400"
    return locations.countries()


@router.get("/states")
def states(response: Response, country: str = Query(max_length=80)):
    code = locations.country_code(country)
    if not code:
        raise HTTPException(422, "Select a recognized country.")
    response.headers["Cache-Control"] = "public, max-age=86400"
    return locations.states(code)


@router.get("/cities")
def cities(response: Response, country: str = Query(max_length=80),
           state: str | None = Query(default=None, max_length=120), q: str = Query(default="", max_length=120)):
    code = locations.country_code(country)
    if not code:
        raise HTTPException(422, "Select a recognized country.")
    st = locations.state_code(code, state) if state else None
    if state and not st:
        raise HTTPException(422, "Select a province belonging to the selected country.")
    response.headers["Cache-Control"] = "public, max-age=3600"
    return locations.search_cities(code, st, q)
