"""Gazetteer: named coastal places -> coordinates.

DELIBERATELY A LOOKUP TABLE, NOT A GEOCODER, AND NEVER THE LLM.

If the model were allowed to answer "where is Rameswaram?", it would be
PRODUCING A NUMBER -- a latitude and a longitude -- and every downstream figure
would inherit that invention. A hallucinated coordinate is the worst possible
version of the rule this system exists to enforce, because the resulting risk
number is perfectly well-formed and describes the wrong patch of ocean.

So: an exact lookup, or an explicit failure that asks the user. Never a guess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Place:
    key: str
    name: str
    lat: float
    lon: float
    note: str = ""


# Offshore points where a boat would actually be, not town centres: a risk
# query about "Kochi" means the fishing grounds off Kochi, and the marine model
# has no data on land.
PLACES: tuple[Place, ...] = (
    Place("kasaragod", "Kasaragod", 12.50, 74.80),
    Place("kannur", "Kannur", 12.00, 74.95),
    Place("vadakara", "Vadakara", 11.50, 75.35),
    Place("kozhikode", "Kozhikode (Calicut)", 11.00, 75.60),
    Place("ponnani", "Ponnani", 10.50, 75.80),
    Place("kochi", "Kochi (Cochin)", 10.00, 76.00),
    Place("alappuzha", "Alappuzha (Alleppey)", 9.50, 76.10),
    Place("kollam", "Kollam", 9.00, 76.25),
    Place("thiruvananthapuram", "Thiruvananthapuram", 8.50, 76.70),
    Place("kanyakumari", "Kanyakumari", 8.10, 77.10),
    Place("tiruchendur", "Tiruchendur", 8.30, 78.10),
    Place("tuticorin", "Tuticorin (Thoothukudi)", 8.80, 78.40),
    Place("gulf_of_mannar", "Gulf of Mannar", 9.30, 79.30,
          "near the Marine National Park"),
    Place("rameswaram", "Rameswaram", 9.80, 79.55),
    Place("katchatheevu", "Katchatheevu", 9.3833, 79.5183,
          "on the India–Sri Lanka maritime boundary"),
    Place("palk_strait", "Palk Strait", 10.30, 80.05),
    Place("nagapattinam", "Nagapattinam", 10.80, 80.05),
    Place("karaikal", "Karaikal", 11.30, 80.00),
    Place("cuddalore", "Cuddalore", 11.80, 80.00),
    Place("puducherry", "Puducherry (Pondicherry)", 12.30, 80.25),
    Place("chennai", "Chennai (Madras)", 12.90, 80.50),
)

_BY_KEY = {p.key: p for p in PLACES}

# Alternate spellings and older names. Anything not here is a hard failure.
_ALIASES = {
    "cochin": "kochi", "calicut": "kozhikode", "alleppey": "alappuzha",
    "madras": "chennai", "pondicherry": "puducherry", "trivandrum":
    "thiruvananthapuram", "thoothukudi": "tuticorin", "quilon": "kollam",
    "cape comorin": "kanyakumari", "kachchatheevu": "katchatheevu",
    "katchatheevu island": "katchatheevu", "mannar": "gulf_of_mannar",
    "palk bay": "palk_strait", "rameshwaram": "rameswaram",
}

_LATLON = re.compile(
    r"^\s*(-?\d+(?:\.\d+)?)\s*[,\s]\s*(-?\d+(?:\.\d+)?)\s*$"
)


class UnknownPlace(LookupError):
    """Raised when a place cannot be resolved. Carries the known list so the
    agent can ask a useful question instead of inventing coordinates."""

    def __init__(self, requested: str) -> None:
        self.requested = requested
        self.known = [p.name for p in PLACES]
        super().__init__(
            f"Unknown place {requested!r}. ORCA has no geocoder by design -- a "
            f"guessed coordinate would silently move the answer to a different "
            f"patch of ocean. Known places: {', '.join(self.known)}. "
            f"A 'lat,lon' pair is also accepted."
        )


def resolve(text: str) -> Place:
    """Named place or 'lat,lon' -> Place. Raises UnknownPlace. Never guesses."""
    raw = (text or "").strip()
    if not raw:
        raise UnknownPlace(text)

    m = _LATLON.match(raw)
    if m:
        lat, lon = float(m.group(1)), float(m.group(2))
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise UnknownPlace(raw)
        return Place(key=f"{lat},{lon}", name=f"{lat:.4f}, {lon:.4f}",
                     lat=lat, lon=lon, note="user-supplied coordinates")

    norm = re.sub(r"[^a-z0-9 ]+", "", raw.lower()).strip()
    key = _ALIASES.get(norm, norm.replace(" ", "_"))
    if key in _BY_KEY:
        return _BY_KEY[key]

    # Unambiguous prefix match only. Two candidates is a question, not a coin flip.
    hits = [p for p in PLACES if p.key.startswith(key) or key in p.name.lower()]
    if len(hits) == 1:
        return hits[0]
    raise UnknownPlace(raw)


def known_places() -> list[str]:
    return [p.name for p in PLACES]
