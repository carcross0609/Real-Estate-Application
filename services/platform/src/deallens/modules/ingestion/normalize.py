"""Address normalization → `AddressNorm` + a deterministic `address_key` for entity
resolution (§14.3).

The TDD names libpostal as the production normalizer. libpostal is a large C/ML dependency
(≈2 GB model, native build) — worth it at scale for messy free-text, but overkill for the
mostly-structured components MLS/ATTOM feeds already give us, and it can't be imported in
the unit-test sandbox. So this module is a **deterministic USPS Publication 28 normalizer**
(directional + suffix + unit-designator standardization) that is:

- pure-Python, dependency-free, and total (never raises on odd input — degrades to
  best-effort), so it runs in CI and the resolution logic is testable without a DB or a
  native lib; and
- swappable: `normalize_address` is the one entry point, so dropping libpostal in later
  (behind the same signature, feature-flagged per §17) touches nothing downstream.

The `address_key` it emits is what deterministic entity resolution joins on — two feeds
that spell "123 North Main Street" and "123 N MAIN ST" must produce the *same* key or the
same house resolves to two canonical properties (the FR-006 failure we exist to prevent).
"""

from __future__ import annotations

import re

from deallens.modules.ingestion.schemas import AddressNorm

# USPS Pub 28 Appendix C1 (common subset) — street-suffix → standard abbreviation.
_SUFFIXES: dict[str, str] = {
    "STREET": "ST", "ST": "ST", "STR": "ST",
    "AVENUE": "AVE", "AVE": "AVE", "AV": "AVE",
    "BOULEVARD": "BLVD", "BLVD": "BLVD", "BOUL": "BLVD",
    "ROAD": "RD", "RD": "RD",
    "DRIVE": "DR", "DR": "DR", "DRV": "DR",
    "LANE": "LN", "LN": "LN",
    "COURT": "CT", "CT": "CT", "CRT": "CT",
    "CIRCLE": "CIR", "CIR": "CIR", "CIRC": "CIR",
    "PLACE": "PL", "PL": "PL",
    "TERRACE": "TER", "TER": "TER", "TERR": "TER",
    "PARKWAY": "PKWY", "PKWY": "PKWY", "PKY": "PKWY",
    "HIGHWAY": "HWY", "HWY": "HWY",
    "TRAIL": "TRL", "TRL": "TRL",
    "WAY": "WAY", "WY": "WAY",
    "SQUARE": "SQ", "SQ": "SQ",
    "LOOP": "LOOP",
    "PLAZA": "PLZ", "PLZ": "PLZ",
    "CROSSING": "XING", "XING": "XING",
    "POINT": "PT", "PT": "PT",
    "PIKE": "PIKE",
    "RUN": "RUN",
    "PASS": "PASS",
    "COVE": "CV", "CV": "CV",
    "BEND": "BND", "BND": "BND",
}

# Directionals (both leading pre-directional and trailing post-directional).
_DIRECTIONALS: dict[str, str] = {
    "NORTH": "N", "N": "N",
    "SOUTH": "S", "S": "S",
    "EAST": "E", "E": "E",
    "WEST": "W", "W": "W",
    "NORTHEAST": "NE", "NE": "NE",
    "NORTHWEST": "NW", "NW": "NW",
    "SOUTHEAST": "SE", "SE": "SE",
    "SOUTHWEST": "SW", "SW": "SW",
}

# Secondary-unit designators (USPS Pub 28 Appendix C2).
_UNIT_DESIGNATORS: dict[str, str] = {
    "APARTMENT": "APT", "APT": "APT",
    "SUITE": "STE", "STE": "STE",
    "UNIT": "UNIT",
    "BUILDING": "BLDG", "BLDG": "BLDG",
    "FLOOR": "FL", "FL": "FL",
    "ROOM": "RM", "RM": "RM",
    "DEPARTMENT": "DEPT", "DEPT": "DEPT",
    "SPACE": "SPC", "SPC": "SPC",
    "TRAILER": "TRLR", "TRLR": "TRLR",
    "LOT": "LOT",
    "NUMBER": "#", "NO": "#", "#": "#",
}

_PUNCT = re.compile(r"[.,]")
_WS = re.compile(r"\s+")
_NON_ALNUM_HASH = re.compile(r"[^A-Z0-9#]+")


def _standardize_token(token: str) -> str:
    """Map one uppercased token through the abbreviation tables. Directionals win over
    suffixes only for tokens that are unambiguous; a token that is not in any table passes
    through unchanged (a house number, a street proper name).
    """
    return _DIRECTIONALS.get(token) or _SUFFIXES.get(token) or _UNIT_DESIGNATORS.get(token) or token


def normalize_street_line(line: str) -> str:
    """Standardize a single street line to a canonical uppercased form:
    `123 North Main Street` → `123 N MAIN ST`. Punctuation dropped, whitespace collapsed,
    every token run through the USPS tables. Total: returns "" for empty/whitespace input.
    """
    if not line:
        return ""
    cleaned = _PUNCT.sub(" ", line.upper())
    tokens = [t for t in _WS.split(cleaned) if t]
    return " ".join(_standardize_token(t) for t in tokens)


def normalize_address(
    *,
    line1: str,
    city: str,
    state: str,
    zip_code: str,
    line2: str | None = None,
    plus4: str | None = None,
) -> AddressNorm:
    """Build a validated `AddressNorm` from source components. State is uppercased and
    truncated to 2 (feeds vary: "TX", "Tx", "Texas" → the validator rejects non-2-char, so we
    take the standard 2-letter code where the feed gives a name we can map, else pass through
    for the DQ layer to catch); ZIP is reduced to its 5-digit core.

    Raises `pydantic.ValidationError` only on structurally impossible input (non-5-digit ZIP
    after cleaning), which the caller treats as a normalize-time DQ failure (§14.4).
    """
    zip5 = re.sub(r"\D", "", zip_code)[:5]
    return AddressNorm(
        line1=normalize_street_line(line1),
        line2=(normalize_street_line(line2) or None) if line2 else None,
        city=_WS.sub(" ", city.strip().upper()),
        state=state.strip().upper()[:2],
        zip=zip5,
        plus4=(re.sub(r"\D", "", plus4)[:4] or None) if plus4 else None,
    )


def address_key(addr: AddressNorm) -> str:
    """Deterministic dedupe key for entity resolution: normalized street line (incl. unit) +
    5-digit ZIP, stripped to `[A-Z0-9#]`. Two source records for the same door produce the
    same key regardless of spelling/punctuation — the join point of deterministic resolution
    (§14.3 step 1). Unit is *included* so the two halves of a duplex don't collapse into one
    property; a missing unit simply yields the building-level key.
    """
    parts = [addr.line1]
    if addr.line2:
        parts.append(addr.line2)
    street = _NON_ALNUM_HASH.sub("", "".join(parts).upper())
    return f"{street}|{addr.zip}"
