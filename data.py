"""Data fetching + on-disk caching.

Sources
  * Baseball Savant (Statcast pitch-by-pitch) via pybaseball
  * FanGraphs leaderboards (WAR, wRC+, FIP, ...) via the FanGraphs JSON API,
    with pybaseball's scraper as a fallback
  * Chadwick register (player names / ID crosswalk) via pybaseball

Everything is cached under ./cache as pickles. Past seasons are cached forever;
the current season refreshes after CURRENT_SEASON_MAX_AGE_HOURS.
"""
from __future__ import annotations

import datetime as dt
import os
import threading
import time
from pathlib import Path

import pandas as pd

from teams import canon, FG_TO_CANON

CACHE_DIR = Path(os.environ.get("SAVANTREF_CACHE", Path(__file__).parent / "cache"))
CACHE_DIR.mkdir(parents=True, exist_ok=True)
CURRENT_SEASON_MAX_AGE_HOURS = 6
FIRST_STATCAST_YEAR = 2008  # pitch-level data exists from 2008; EV/LA from 2015

_lock = threading.Lock()
_mem: dict[str, pd.DataFrame] = {}


# ----------------------------------------------------------------- helpers
def _today():
    return dt.date.today()


def _is_current(year: int) -> bool:
    return year >= _today().year


def _cache_path(key: str) -> Path:
    return CACHE_DIR / f"{key}.pkl"


def _fresh(path: Path, year: int | None) -> bool:
    if not path.exists():
        return False
    if year is None or not _is_current(year):
        return True
    age_h = (time.time() - path.stat().st_mtime) / 3600
    return age_h < CURRENT_SEASON_MAX_AGE_HOURS


def _cached(key: str, year: int | None, loader, refresh: bool = False) -> pd.DataFrame:
    path = _cache_path(key)
    with _lock:
        if not refresh and key in _mem and _fresh(path, year):
            return _mem[key]
        if not refresh and _fresh(path, year):
            df = pd.read_pickle(path)
            _mem[key] = df
            return df
    df = loader()
    if df is None:
        df = pd.DataFrame()
    with _lock:
        df.to_pickle(path)
        _mem[key] = df
    return df


def _pyb():
    import pybaseball
    try:
        pybaseball.cache.enable()
    except Exception:
        pass
    return pybaseball


def _clip_end(end: str) -> str:
    e = dt.date.fromisoformat(end)
    return min(e, _today()).isoformat()


KEEP_COLS = [
    "game_pk", "game_date", "game_type", "home_team", "away_team", "inning", "inning_topbot",
    "outs_when_up", "at_bat_number", "pitch_number", "batter", "pitcher", "player_name", "stand", "p_throws",
    "events", "description", "type", "des", "bb_type", "balls", "strikes", "on_1b", "on_2b", "on_3b",
    "bat_score", "post_bat_score", "home_score", "away_score", "post_home_score", "post_away_score",
    "launch_speed", "launch_angle", "launch_speed_angle", "woba_value", "woba_denom",
    "estimated_woba_using_speedangle", "estimated_ba_using_speedangle", "estimated_slg_using_speedangle",
    "home_win_exp", "delta_home_win_exp", "delta_run_exp", "pitch_type", "release_speed",
    "fielder_2", "fielder_3", "fielder_4", "fielder_5", "fielder_6", "fielder_7", "fielder_8", "fielder_9",
    "zone", "pitch_name", "release_spin_rate", "release_extension", "pfx_x", "pfx_z", "hc_x", "hc_y",
]


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    df = df[[c for c in KEEP_COLS if c in df.columns]].copy()
    for c in ("home_team", "away_team"):
        if c in df:
            df[c] = df[c].map(canon)
    if "game_date" in df:
        df["game_date"] = pd.to_datetime(df["game_date"])
    df = df.drop_duplicates(subset=["game_pk", "at_bat_number", "pitch_number"])
    return df.sort_values(["game_date", "game_pk", "at_bat_number", "pitch_number"]).reset_index(drop=True)


# ----------------------------------------------------------------- Savant
def get_postseason(year: int, refresh: bool = False) -> pd.DataFrame:
    """All postseason pitches for a season (game_type F/D/L/W)."""
    def load():
        start = f"{year}-09-20"
        end = _clip_end(f"{year}-11-20")
        if start > end:
            return pd.DataFrame()
        df = _pyb().statcast(start_dt=start, end_dt=end, verbose=False)
        if df is None or df.empty:
            return pd.DataFrame()
        df = df[df["game_type"].isin(["F", "D", "L", "W"])]
        return _normalize(df)
    return _cached(f"post_{year}", year, load, refresh)


def get_team_season(team: str, year: int, refresh: bool = False) -> pd.DataFrame:
    """All regular-season pitches in games involving `team`."""
    team = canon(team)
    lg = _mem.get(f"reg_{year}")
    if lg is not None and not lg.empty:
        return lg[(lg["home_team"] == team) | (lg["away_team"] == team)]

    def load():
        start = f"{year}-03-01"
        end = _clip_end(f"{year}-10-10")
        if start > end:
            return pd.DataFrame()
        pyb = _pyb()
        df = None
        # Savant has used both AZ/ARI and ATH/OAK; try the likely spelling first.
        tries = {"AZ": ["AZ", "ARI"], "ATH": ["ATH", "OAK"] if year >= 2025 else ["OAK", "ATH"]}.get(team, [team])
        for t in tries:
            df = pyb.statcast(start_dt=start, end_dt=end, team=t, verbose=False)
            if df is not None and not df.empty:
                break
        if df is None or df.empty:
            return pd.DataFrame()
        df = df[df["game_type"] == "R"]
        df = _normalize(df)
        return df[(df["home_team"] == team) | (df["away_team"] == team)]
    return _cached(f"team_{team}_{year}", year, load, refresh)


REG_WINDOW = {2020: ("07-20", "09-28")}
FINAL_AFTER_DAYS = 3   # a day's games are treated as final (cached for good) after this many days


def _day(d: dt.date, refresh: bool = False) -> pd.DataFrame:
    """Every pitch of one day (all teams). Days older than FINAL_AFTER_DAYS are cached forever."""
    final = (_today() - d).days > FINAL_AFTER_DAYS
    key = f"day_{d.isoformat()}"

    def load():
        df = _pyb().statcast(start_dt=d.isoformat(), end_dt=d.isoformat(), verbose=False, parallel=False)
        return _normalize(df)
    return _cached(key, None if final else d.year, load, refresh and not final)


def get_regular_season(year: int, refresh: bool = False, workers: int = 6) -> pd.DataFrame:
    """Every regular-season pitch for every team (used by the static-site build)."""
    from concurrent.futures import ThreadPoolExecutor
    key = f"reg_{year}"
    if key in _mem and not refresh:
        return _mem[key]
    lo, hi = REG_WINDOW.get(year, ("03-15", "10-06"))
    start, end = dt.date.fromisoformat(f"{year}-{lo}"), min(dt.date.fromisoformat(f"{year}-{hi}"), _today())
    days = [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]
    frames, failed = [], []

    def one(d):
        try:
            return _day(d, refresh)
        except Exception as e:  # noqa: BLE001
            failed.append((d, e))
            return pd.DataFrame()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for df in ex.map(one, days):
            if df is not None and not df.empty:
                frames.append(df[df["game_type"] == "R"])
    if failed:
        print(f"[savant] {len(failed)} day(s) of {year} failed to download, e.g. {failed[0][0]}: {failed[0][1]}")
    df = _normalize(pd.concat(frames, ignore_index=True)) if frames else pd.DataFrame()
    _mem[key] = df
    return df


def get_game(game_pk: int, year: int | None = None, team: str | None = None) -> pd.DataFrame:
    """One game's pitches. Reuses a season cache when we know where to look."""
    game_pk = int(game_pk)
    lg = _mem.get(f"reg_{year}") if year else None
    if lg is not None and not lg.empty:
        g = lg[lg["game_pk"] == game_pk]
        if not g.empty:
            return g.reset_index(drop=True)
    candidates = []
    if year:
        candidates.append(f"post_{year}")
        if team:
            candidates.append(f"team_{canon(team)}_{year}")
    for key in candidates:
        path = _cache_path(key)
        if key in _mem or path.exists():
            df = _mem.get(key)
            if df is None:
                df = pd.read_pickle(path)
                _mem[key] = df
            if not df.empty:
                g = df[df["game_pk"] == game_pk]
                if not g.empty:
                    return g.reset_index(drop=True)

    def load():
        return _normalize(_pyb().statcast_single_game(game_pk))
    return _cached(f"game_{game_pk}", year, load)


# ----------------------------------------------------------------- names
def get_names() -> dict[int, str]:
    key = "names"
    if key in _mem:
        return _mem[key]

    def load():
        reg = _pyb().chadwick_register()
        reg = reg[reg["key_mlbam"].notna() & (reg["key_mlbam"] > 0)]
        out = pd.DataFrame({
            "mlbam": reg["key_mlbam"].astype(int),
            "fg": reg.get("key_fangraphs"),
            "name": (reg["name_first"].fillna("").str.title() + " " + reg["name_last"].fillna("").str.title()).str.strip(),
        })
        return out
    try:
        reg = _cached("chadwick", None, load)
        names = dict(zip(reg["mlbam"], reg["name"]))
    except Exception:
        names = {}
    _mem[key] = names
    return names


def fg_to_mlbam() -> dict[int, int]:
    try:
        get_names()
        reg = _mem.get("chadwick")
        if reg is None or reg.empty:
            return {}
        reg = reg[reg["fg"].notna() & (reg["fg"] > 0)]
        return dict(zip(reg["fg"].astype(int), reg["mlbam"].astype(int)))
    except Exception:
        return {}


# ----------------------------------------------------------------- FanGraphs
_FG_URL = "https://www.fangraphs.com/api/leaders/major-league/data"
_FG_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json",
    "Referer": "https://www.fangraphs.com/leaders/major-league",
}

FG_BAT_COLS = ["WAR", "wRC+", "wOBA", "BABIP", "ISO", "Off", "Def", "BsR", "Spd", "R", "SB", "CS", "Age", "PA"]
FG_PIT_COLS = ["WAR", "ERA", "FIP", "xFIP", "SIERA", "ERA-", "FIP-", "LOB%", "GB%", "ER", "Age", "IP"]


def _first(df, names):
    for n in names:
        if n in df.columns:
            return df[n]
    return pd.Series([None] * len(df), index=df.index)


def _fg_api(kind: str, year: int) -> pd.DataFrame:
    import requests
    params = {
        "age": "", "pos": "all", "stats": kind, "lg": "all", "qual": "0",
        "season": year, "season1": year, "startdate": "", "enddate": "",
        "month": "0", "hand": "", "team": "0", "pageitems": "2000000000",
        "pagenum": "1", "ind": "0", "rost": "0", "players": "", "type": "8",
        "postseason": "", "sortdir": "default", "sortstat": "WAR",
    }
    r = requests.get(_FG_URL, params=params, headers=_FG_HEADERS, timeout=60)
    r.raise_for_status()
    js = r.json()
    rows = js.get("data", js if isinstance(js, list) else [])
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    out = pd.DataFrame({
        "mlbam": pd.to_numeric(_first(df, ["xMLBAMID", "MLBAMID", "mlbamid"]), errors="coerce"),
        "fgid": pd.to_numeric(_first(df, ["playerid", "playerids", "IDfg"]), errors="coerce"),
        "fg_name": _first(df, ["PlayerName", "Name", "PlayerNameRoute"]),
        "fg_team": _first(df, ["TeamNameAbb", "TeamName", "Team"]),
    })
    for c in (FG_BAT_COLS if kind == "bat" else FG_PIT_COLS):
        if c in df.columns:
            out[c] = pd.to_numeric(df[c], errors="coerce")
    return out


def _fg_pybaseball(kind: str, year: int) -> pd.DataFrame:
    pyb = _pyb()
    df = pyb.batting_stats(year, qual=0) if kind == "bat" else pyb.pitching_stats(year, qual=0)
    xw = fg_to_mlbam()
    out = pd.DataFrame({
        "fgid": pd.to_numeric(df["IDfg"], errors="coerce"),
        "fg_name": df["Name"],
        "fg_team": df["Team"],
    })
    out["mlbam"] = out["fgid"].map(xw)
    for c in (FG_BAT_COLS if kind == "bat" else FG_PIT_COLS):
        if c in df.columns:
            out[c] = pd.to_numeric(df[c], errors="coerce")
    return out


def get_fangraphs(kind: str, year: int) -> pd.DataFrame:
    """kind = 'bat' or 'pit'. Returns one row per player with an `mlbam` id.
    Never raises: an empty frame means FanGraphs couldn't be reached."""
    def load():
        for fn in (_fg_api, _fg_pybaseball):
            try:
                df = fn(kind, year)
                if df is not None and not df.empty:
                    if df["mlbam"].isna().all():
                        xw = fg_to_mlbam()
                        df["mlbam"] = df["fgid"].map(xw)
                    df = df[df["mlbam"].notna()].copy()
                    df["mlbam"] = df["mlbam"].astype(int)
                    df["fg_team"] = df["fg_team"].astype(str).map(lambda t: FG_TO_CANON.get(t, t))
                    return df.drop_duplicates("mlbam")
            except Exception as e:  # noqa: BLE001
                print(f"[fangraphs] {fn.__name__} failed for {kind} {year}: {e}")
        return pd.DataFrame()
    df = _cached(f"fg_{kind}_{year}", year, load)
    if df.empty:
        # don't keep an empty result around forever
        try:
            _cache_path(f"fg_{kind}_{year}").unlink()
            _mem.pop(f"fg_{kind}_{year}", None)
        except Exception:
            pass
    return df


def cached_seasons() -> list[int]:
    yrs = set()
    for p in CACHE_DIR.glob("post_*.pkl"):
        try:
            yrs.add(int(p.stem.split("_")[1]))
        except ValueError:
            pass
    return sorted(yrs, reverse=True)
