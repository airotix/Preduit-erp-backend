"""AQL sampling engine — ISO 2859-1 / ANSI-ASQ Z1.4, single sampling plan,
General Inspection Level II (the industry default for apparel/consumer goods).

Given a lot (production) quantity and an AQL level, it returns the sample size
to inspect and the acceptance number (maximum allowed defects). One defect over
that number rejects the lot.

The tables are declared as plain data so they stay auditable and configurable —
edit LOT_SIZE_CODES / ACCEPTANCE to change the plan without touching logic.
"""
from __future__ import annotations

# Lot-size ranges → code letter (General Inspection Level II).
# Each tuple: (inclusive_upper_bound, code_letter). Last entry is the catch-all.
LOT_SIZE_CODES: list[tuple[int, str]] = [
    (8, "A"), (15, "B"), (25, "C"), (50, "D"), (90, "E"), (150, "F"),
    (280, "G"), (500, "H"), (1200, "J"), (3200, "K"), (10000, "L"),
    (35000, "M"), (150000, "N"), (500000, "P"), (10**12, "Q"),
]

# Code letter → sample size (single sampling, Level II).
SAMPLE_SIZE: dict[str, int] = {
    "A": 2, "B": 3, "C": 5, "D": 8, "E": 13, "F": 20, "G": 32, "H": 50,
    "J": 80, "K": 125, "L": 200, "M": 315, "N": 500, "P": 800, "Q": 1250,
}

# Acceptance number (max allowed defects) by (code letter, AQL level).
# Values follow ANSI/ASQ Z1.4 single-sampling normal inspection. When a plan
# cell uses an arrow (sample-size shift) in the standard, we use the nearest
# practical acceptance number so the engine always returns a usable Ac.
ACCEPTANCE: dict[str, dict[str, int]] = {
    #        AQL:  1.0  1.5  2.5  4.0  6.5
    "A": {"1.0": 0, "1.5": 0, "2.5": 0, "4.0": 0, "6.5": 0},
    "B": {"1.0": 0, "1.5": 0, "2.5": 0, "4.0": 0, "6.5": 1},
    "C": {"1.0": 0, "1.5": 0, "2.5": 0, "4.0": 1, "6.5": 1},
    "D": {"1.0": 0, "1.5": 0, "2.5": 1, "4.0": 1, "6.5": 2},
    "E": {"1.0": 0, "1.5": 1, "2.5": 1, "4.0": 2, "6.5": 3},
    "F": {"1.0": 1, "1.5": 1, "2.5": 2, "4.0": 3, "6.5": 5},
    "G": {"1.0": 1, "1.5": 2, "2.5": 3, "4.0": 5, "6.5": 7},
    "H": {"1.0": 2, "1.5": 3, "2.5": 5, "4.0": 7, "6.5": 10},
    "J": {"1.0": 3, "1.5": 5, "2.5": 7, "4.0": 10, "6.5": 14},
    "K": {"1.0": 5, "1.5": 7, "2.5": 10, "4.0": 14, "6.5": 21},
    "L": {"1.0": 7, "1.5": 10, "2.5": 14, "4.0": 21, "6.5": 21},
    "M": {"1.0": 10, "1.5": 14, "2.5": 21, "4.0": 21, "6.5": 21},
    "N": {"1.0": 14, "1.5": 21, "2.5": 21, "4.0": 21, "6.5": 21},
    "P": {"1.0": 21, "1.5": 21, "2.5": 21, "4.0": 21, "6.5": 21},
    "Q": {"1.0": 21, "1.5": 21, "2.5": 21, "4.0": 21, "6.5": 21},
}

SUPPORTED_AQL = ("1.0", "1.5", "2.5", "4.0", "6.5")


def _normalise_aql(aql: str | None) -> str:
    """Coerce loose input ('2.5', '2,5', '4', 4.0) to a supported AQL string."""
    if aql is None:
        return "2.5"
    s = str(aql).strip().replace(",", ".")
    try:
        s = f"{float(s):.1f}"
    except ValueError:
        return "2.5"
    return s if s in SUPPORTED_AQL else "2.5"


def code_letter(lot_qty: int) -> str:
    for upper, letter in LOT_SIZE_CODES:
        if lot_qty <= upper:
            return letter
    return "Q"


def sampling_plan(lot_qty: int | None, aql: str | None) -> dict:
    """Return the sampling plan for a lot: sample size + max allowed defects.

    { aql, lotQty, codeLetter, sampleSize, maxDefects }.
    When lot_qty is unknown/0 we return a plan with no sample (sampleSize 0),
    so the caller can still render the panel and fill it once qty is known.
    """
    aql_n = _normalise_aql(aql)
    qty = int(lot_qty or 0)
    if qty <= 0:
        return {"aql": aql_n, "lotQty": qty, "codeLetter": None,
                "sampleSize": 0, "maxDefects": 0}
    letter = code_letter(qty)
    sample = min(SAMPLE_SIZE.get(letter, 0), qty)   # never sample more than the lot
    accept = ACCEPTANCE.get(letter, {}).get(aql_n, 0)
    return {"aql": aql_n, "lotQty": qty, "codeLetter": letter,
            "sampleSize": sample, "maxDefects": accept}


def acceptance_for_sample(sample_size: int | None, aql: str | None) -> int:
    """Acceptance number (max allowed defects) for a MANUALLY chosen sample size.
    We map the entered sample size to the nearest standard code-letter sample and
    read that letter's acceptance number for the AQL — so an override still gets a
    sensible, standards-aligned limit."""
    aql_n = _normalise_aql(aql)
    n = int(sample_size or 0)
    if n <= 0:
        return 0
    letter = min(SAMPLE_SIZE.items(), key=lambda kv: abs(kv[1] - n))[0]
    return ACCEPTANCE.get(letter, {}).get(aql_n, 0)


def evaluate(lot_qty: int | None, aql: str | None, actual_defects: int) -> dict:
    """Full AQL evaluation for a lot given the observed defect count."""
    plan = sampling_plan(lot_qty, aql)
    accepted = int(actual_defects or 0) <= plan["maxDefects"]
    return {**plan, "actualDefects": int(actual_defects or 0),
            "accepted": accepted, "result": "Pass" if accepted else "Fail"}
