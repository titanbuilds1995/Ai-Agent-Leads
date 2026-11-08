"""Florida city → county, statewide (the residential engine only knows the three South Florida counties)."""
from __future__ import annotations

import re

from ..util import COUNTY_BY_CITY

_FL: dict[str, str] = {
    # Northeast
    "jacksonville": "Duval", "jacksonville beach": "Duval", "atlantic beach": "Duval", "neptune beach": "Duval", "baldwin": "Duval",
    "orange park": "Clay", "fleming island": "Clay", "green cove springs": "Clay", "middleburg": "Clay", "keystone heights": "Clay",
    "saint augustine": "St. Johns", "st augustine": "St. Johns", "st. augustine": "St. Johns", "ponte vedra beach": "St. Johns",
    "ponte vedra": "St. Johns", "saint johns": "St. Johns", "st johns": "St. Johns", "st. johns": "St. Johns", "nocatee": "St. Johns",
    "fernandina beach": "Nassau", "yulee": "Nassau", "callahan": "Nassau", "hilliard": "Nassau",
    "macclenny": "Baker", "palatka": "Putnam", "interlachen": "Putnam", "crescent city": "Putnam",
    "starke": "Bradford", "lake butler": "Union", "jasper": "Hamilton", "live oak": "Suwannee", "lake city": "Columbia",
    "madison": "Madison", "perry": "Taylor", "mayo": "Lafayette", "cross city": "Dixie", "trenton": "Gilchrist", "bronson": "Levy",
    "chiefland": "Levy", "williston": "Levy", "cedar key": "Levy",
    "gainesville": "Alachua", "alachua": "Alachua", "high springs": "Alachua", "newberry": "Alachua", "archer": "Alachua", "hawthorne": "Alachua",
    # Northwest / Panhandle
    "tallahassee": "Leon", "crawfordville": "Wakulla", "quincy": "Gadsden", "havana": "Gadsden", "monticello": "Jefferson",
    "marianna": "Jackson", "chipley": "Washington", "bonifay": "Holmes", "blountstown": "Calhoun", "bristol": "Liberty",
    "port st joe": "Gulf", "port saint joe": "Gulf", "apalachicola": "Franklin", "carrabelle": "Franklin",
    "panama city": "Bay", "panama city beach": "Bay", "lynn haven": "Bay", "callaway": "Bay", "springfield": "Bay",
    "fort walton beach": "Okaloosa", "destin": "Okaloosa", "niceville": "Okaloosa", "crestview": "Okaloosa", "mary esther": "Okaloosa",
    "shalimar": "Okaloosa", "miramar beach": "Walton", "santa rosa beach": "Walton", "defuniak springs": "Walton", "freeport": "Walton",
    "inlet beach": "Walton", "pensacola": "Escambia", "cantonment": "Escambia", "century": "Escambia", "pensacola beach": "Escambia",
    "milton": "Santa Rosa", "pace": "Santa Rosa", "navarre": "Santa Rosa", "gulf breeze": "Santa Rosa",
    # Central
    "orlando": "Orange", "winter park": "Orange", "apopka": "Orange", "ocoee": "Orange", "winter garden": "Orange", "maitland": "Orange",
    "windermere": "Orange", "oakland": "Orange", "edgewood": "Orange", "belle isle": "Orange", "eatonville": "Orange", "lake buena vista": "Orange",
    "kissimmee": "Osceola", "saint cloud": "Osceola", "st cloud": "Osceola", "st. cloud": "Osceola", "celebration": "Osceola", "poinciana": "Osceola",
    "sanford": "Seminole", "altamonte springs": "Seminole", "casselberry": "Seminole", "longwood": "Seminole", "oviedo": "Seminole",
    "winter springs": "Seminole", "lake mary": "Seminole", "heathrow": "Seminole",
    "clermont": "Lake", "leesburg": "Lake", "eustis": "Lake", "mount dora": "Lake", "tavares": "Lake", "lady lake": "Lake",
    "groveland": "Lake", "minneola": "Lake", "umatilla": "Lake", "fruitland park": "Lake", "the villages": "Sumter", "wildwood": "Sumter",
    "bushnell": "Sumter", "webster": "Sumter",
    "ocala": "Marion", "belleview": "Marion", "dunnellon": "Marion", "silver springs": "Marion", "summerfield": "Marion",
    "daytona beach": "Volusia", "deland": "Volusia", "deltona": "Volusia", "ormond beach": "Volusia", "port orange": "Volusia",
    "new smyrna beach": "Volusia", "edgewater": "Volusia", "orange city": "Volusia", "debary": "Volusia", "holly hill": "Volusia",
    "south daytona": "Volusia", "daytona beach shores": "Volusia", "oak hill": "Volusia",
    "palm coast": "Flagler", "bunnell": "Flagler", "flagler beach": "Flagler",
    "titusville": "Brevard", "melbourne": "Brevard", "palm bay": "Brevard", "cocoa": "Brevard", "cocoa beach": "Brevard",
    "rockledge": "Brevard", "merritt island": "Brevard", "satellite beach": "Brevard", "indian harbour beach": "Brevard",
    "west melbourne": "Brevard", "cape canaveral": "Brevard", "viera": "Brevard", "mims": "Brevard", "grant": "Brevard",
    "lakeland": "Polk", "winter haven": "Polk", "bartow": "Polk", "haines city": "Polk", "auburndale": "Polk", "lake wales": "Polk",
    "davenport": "Polk", "mulberry": "Polk", "fort meade": "Polk", "frostproof": "Polk", "lake alfred": "Polk", "eagle lake": "Polk",
    "dundee": "Polk", "polk city": "Polk",
    "sebring": "Highlands", "avon park": "Highlands", "lake placid": "Highlands", "wauchula": "Hardee", "arcadia": "DeSoto",
    "okeechobee": "Okeechobee", "labelle": "Hendry", "clewiston": "Hendry", "moore haven": "Glades",
    # Tampa Bay
    "tampa": "Hillsborough", "brandon": "Hillsborough", "riverview": "Hillsborough", "plant city": "Hillsborough", "temple terrace": "Hillsborough",
    "valrico": "Hillsborough", "ruskin": "Hillsborough", "apollo beach": "Hillsborough", "lutz": "Hillsborough", "seffner": "Hillsborough",
    "gibsonton": "Hillsborough", "sun city center": "Hillsborough", "wimauma": "Hillsborough", "odessa": "Hillsborough", "dover": "Hillsborough",
    "lithia": "Hillsborough", "thonotosassa": "Hillsborough", "town n country": "Hillsborough", "carrollwood": "Hillsborough",
    "st petersburg": "Pinellas", "st. petersburg": "Pinellas", "saint petersburg": "Pinellas", "clearwater": "Pinellas", "largo": "Pinellas",
    "pinellas park": "Pinellas", "dunedin": "Pinellas", "tarpon springs": "Pinellas", "palm harbor": "Pinellas", "seminole": "Pinellas",
    "oldsmar": "Pinellas", "safety harbor": "Pinellas", "gulfport": "Pinellas", "treasure island": "Pinellas", "madeira beach": "Pinellas",
    "st pete beach": "Pinellas", "saint pete beach": "Pinellas", "indian rocks beach": "Pinellas", "kenneth city": "Pinellas", "belleair": "Pinellas",
    "new port richey": "Pasco", "port richey": "Pasco", "wesley chapel": "Pasco", "zephyrhills": "Pasco", "dade city": "Pasco",
    "land o lakes": "Pasco", "land o' lakes": "Pasco", "hudson": "Pasco", "holiday": "Pasco", "trinity": "Pasco", "spring hill": "Hernando",
    "brooksville": "Hernando", "weeki wachee": "Hernando", "inverness": "Citrus", "crystal river": "Citrus", "homosassa": "Citrus",
    "lecanto": "Citrus", "beverly hills": "Citrus",
    # Southwest
    "bradenton": "Manatee", "palmetto": "Manatee", "lakewood ranch": "Manatee", "parrish": "Manatee", "ellenton": "Manatee",
    "holmes beach": "Manatee", "anna maria": "Manatee", "bradenton beach": "Manatee", "myakka city": "Manatee",
    "sarasota": "Sarasota", "venice": "Sarasota", "north port": "Sarasota", "nokomis": "Sarasota", "osprey": "Sarasota",
    "englewood": "Sarasota", "longboat key": "Sarasota", "siesta key": "Sarasota",
    "punta gorda": "Charlotte", "port charlotte": "Charlotte", "rotonda west": "Charlotte",
    "fort myers": "Lee", "cape coral": "Lee", "bonita springs": "Lee", "estero": "Lee", "lehigh acres": "Lee", "north fort myers": "Lee",
    "fort myers beach": "Lee", "sanibel": "Lee", "alva": "Lee", "san carlos park": "Lee",
    "naples": "Collier", "marco island": "Collier", "immokalee": "Collier", "ave maria": "Collier", "golden gate": "Collier", "everglades city": "Collier",
    # Treasure Coast
    "port st lucie": "St. Lucie", "port saint lucie": "St. Lucie", "port st. lucie": "St. Lucie", "fort pierce": "St. Lucie",
    "ft pierce": "St. Lucie", "stuart": "Martin", "palm city": "Martin", "hobe sound": "Martin", "jensen beach": "Martin",
    "indiantown": "Martin", "vero beach": "Indian River", "sebastian": "Indian River", "fellsmere": "Indian River",
    # Keys
    "key west": "Monroe", "key largo": "Monroe", "marathon": "Monroe", "islamorada": "Monroe", "tavernier": "Monroe",
    "big pine key": "Monroe", "summerland key": "Monroe", "stock island": "Monroe",
}

SOUTH_FLORIDA = {"Miami-Dade", "Broward", "Palm Beach"}

REGION_BY_COUNTY = {
    "Duval": "Northeast", "Clay": "Northeast", "St. Johns": "Northeast", "Nassau": "Northeast", "Baker": "Northeast", "Putnam": "Northeast",
    "Bradford": "Northeast", "Union": "Northeast", "Columbia": "Northeast", "Suwannee": "Northeast", "Hamilton": "Northeast",
    "Alachua": "Northeast", "Levy": "Northeast", "Gilchrist": "Northeast", "Dixie": "Northeast", "Lafayette": "Northeast", "Madison": "Northeast",
    "Taylor": "Northeast", "Flagler": "Northeast",
    "Leon": "Panhandle", "Wakulla": "Panhandle", "Gadsden": "Panhandle", "Jefferson": "Panhandle", "Jackson": "Panhandle",
    "Washington": "Panhandle", "Holmes": "Panhandle", "Calhoun": "Panhandle", "Liberty": "Panhandle", "Gulf": "Panhandle",
    "Franklin": "Panhandle", "Bay": "Panhandle", "Okaloosa": "Panhandle", "Walton": "Panhandle", "Escambia": "Panhandle", "Santa Rosa": "Panhandle",
    "Orange": "Central", "Osceola": "Central", "Seminole": "Central", "Lake": "Central", "Sumter": "Central", "Marion": "Central",
    "Volusia": "Central", "Brevard": "Central", "Polk": "Central", "Highlands": "Central", "Hardee": "Central", "DeSoto": "Central",
    "Okeechobee": "Central", "Hendry": "Central", "Glades": "Central",
    "Hillsborough": "Tampa Bay", "Pinellas": "Tampa Bay", "Pasco": "Tampa Bay", "Hernando": "Tampa Bay", "Citrus": "Tampa Bay",
    "Manatee": "Southwest", "Sarasota": "Southwest", "Charlotte": "Southwest", "Lee": "Southwest", "Collier": "Southwest",
    "St. Lucie": "Treasure Coast", "Martin": "Treasure Coast", "Indian River": "Treasure Coast",
    "Monroe": "Keys", "Miami-Dade": "South Florida", "Broward": "South Florida", "Palm Beach": "South Florida",
}

_COUNTY_WORD = re.compile(r"\b([A-Z][A-Za-z.\- ]{2,20}?) County\b")


def county_for_city(city: str | None) -> str | None:
    if not city:
        return None
    key = re.sub(r"\s+", " ", city.lower().replace(".", "").strip())
    key = key.replace("ft ", "fort ").replace("saint ", "st ")
    return COUNTY_BY_CITY.get(key) or _FL.get(key) or _FL.get(city.lower().strip())


def county_from_text(text: str | None) -> str | None:
    """'…located in Hernando County…' → 'Hernando' (only when the name is one we know)."""
    for m in _COUNTY_WORD.finditer(text or ""):
        name = m.group(1).strip()
        for known in REGION_BY_COUNTY:
            if known.lower().replace(".", "") == name.lower().replace(".", "").replace("saint", "st"):
                return known
    return None


def region_for(county: str | None) -> str:
    return REGION_BY_COUNTY.get(county or "", "Unknown")


def is_south_florida(county: str | None, south: set[str] | list[str] | None = None) -> bool:
    return (county or "") in (set(south) if south else SOUTH_FLORIDA)
