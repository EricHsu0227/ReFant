"""Team metadata: names, leagues, and abbreviation mappings between sources."""

# Canonical abbreviation = Baseball Savant's abbreviation.
TEAMS = {
    "AZ":  ("Arizona Diamondbacks", "NL", "West"),
    "ATL": ("Atlanta Braves", "NL", "East"),
    "BAL": ("Baltimore Orioles", "AL", "East"),
    "BOS": ("Boston Red Sox", "AL", "East"),
    "CHC": ("Chicago Cubs", "NL", "Central"),
    "CWS": ("Chicago White Sox", "AL", "Central"),
    "CIN": ("Cincinnati Reds", "NL", "Central"),
    "CLE": ("Cleveland Guardians", "AL", "Central"),
    "COL": ("Colorado Rockies", "NL", "West"),
    "DET": ("Detroit Tigers", "AL", "Central"),
    "HOU": ("Houston Astros", "AL", "West"),
    "KC":  ("Kansas City Royals", "AL", "Central"),
    "LAA": ("Los Angeles Angels", "AL", "West"),
    "LAD": ("Los Angeles Dodgers", "NL", "West"),
    "MIA": ("Miami Marlins", "NL", "East"),
    "MIL": ("Milwaukee Brewers", "NL", "Central"),
    "MIN": ("Minnesota Twins", "AL", "Central"),
    "NYM": ("New York Mets", "NL", "East"),
    "NYY": ("New York Yankees", "AL", "East"),
    "ATH": ("Athletics", "AL", "West"),
    "PHI": ("Philadelphia Phillies", "NL", "East"),
    "PIT": ("Pittsburgh Pirates", "NL", "Central"),
    "SD":  ("San Diego Padres", "NL", "West"),
    "SEA": ("Seattle Mariners", "AL", "West"),
    "SF":  ("San Francisco Giants", "NL", "West"),
    "STL": ("St. Louis Cardinals", "NL", "Central"),
    "TB":  ("Tampa Bay Rays", "AL", "East"),
    "TEX": ("Texas Rangers", "AL", "West"),
    "TOR": ("Toronto Blue Jays", "AL", "East"),
    "WSH": ("Washington Nationals", "NL", "East"),
}

# Older / alternate spellings seen in Savant data, mapped to canonical.
ALIASES = {
    "ARI": "AZ", "OAK": "ATH", "FLA": "MIA", "CHW": "CWS", "KCR": "KC",
    "SDP": "SD", "SFG": "SF", "TBR": "TB", "TBD": "TB", "WSN": "WSH",
    "WAS": "WSH", "ANA": "LAA", "LAN": "LAD", "NYA": "NYY", "NYN": "NYM",
}

# FanGraphs team abbreviations -> canonical
FG_TO_CANON = {
    "ARI": "AZ", "CHW": "CWS", "KCR": "KC", "SDP": "SD", "SFG": "SF",
    "TBR": "TB", "WSN": "WSH", "OAK": "ATH", "FLA": "MIA",
}

# Historical team names worth showing for older seasons.
_HISTORIC_NAMES = {
    ("CLE", 2021): "Cleveland Indians",
    ("MIA", 2011): "Florida Marlins",
    ("TB", 2007): "Tampa Bay Devil Rays",
    ("ATH", 2024): "Oakland Athletics",
    ("LAA", 2004): "Anaheim Angels",
}


def canon(abbr):
    if abbr is None:
        return None
    a = str(abbr).upper().strip()
    return ALIASES.get(a, a)


def team_name(abbr, year=None):
    a = canon(abbr)
    if year is not None:
        # Use the most recent historic name whose cutoff year >= year.
        best = None
        for (t, cutoff), name in _HISTORIC_NAMES.items():
            if t == a and year <= cutoff and (best is None or cutoff < best[0]):
                best = (cutoff, name)
        if best:
            return best[1]
    return TEAMS.get(a, (a, "", ""))[0]


def league(abbr, year=None):
    a = canon(abbr)
    if a == "HOU" and year is not None and year <= 2012:
        return "NL"
    return TEAMS.get(a, ("", "", ""))[1]


def division(abbr, year=None):
    a = canon(abbr)
    if a == "HOU" and year is not None and year <= 2012:
        return "Central"
    return TEAMS.get(a, ("", "", ""))[2]


ROUND_ORDER = {"F": 0, "D": 1, "L": 2, "W": 3}


def round_name(game_type, year, lg=""):
    if game_type == "W":
        return "World Series"
    if game_type == "L":
        return f"{lg}CS" if lg else "LCS"
    if game_type == "D":
        return f"{lg}DS" if lg else "Division Series"
    if game_type == "F":
        if 2012 <= year <= 2021 and year != 2020:
            return f"{lg} Wild Card Game".strip()
        return f"{lg} Wild Card Series".strip()
    return game_type
