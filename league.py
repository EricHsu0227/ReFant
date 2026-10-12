"""League-wide stats: every batter and pitcher in a season, FanGraphs- and Savant-style.

Everything is computed from Statcast pitch-by-pitch data in one vectorized pass
(no per-player loops), then FanGraphs value stats (WAR, wRC+, FIP, xFIP, SIERA,
ERA, R, SB...) are merged in when they could be downloaded.

    season(year, "R")  -> regular season          season(year, "P") -> postseason
"""
from __future__ import annotations

import math
from functools import lru_cache

import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", category=pd.errors.PerformanceWarning)

import data
import stats as S
from teams import TEAMS, canon, division, league as league_of, team_name

POST = ["F", "D", "L", "W"]
NAN = float("nan")

# ======================================================================= columns
# key -> (label, format, tooltip). Formats: i int, r3 .312, p1 12.3%, f0 f1 f2, ip, wpa
COLS = {
    # --- standard batting
    "G": ("G", "i", "Games"), "PA": ("PA", "i", "Plate appearances"), "AB": ("AB", "i", "At bats"),
    "H": ("H", "i", "Hits"), "1B": ("1B", "i", "Singles"), "2B": ("2B", "i", "Doubles"), "3B": ("3B", "i", "Triples"),
    "HR": ("HR", "i", "Home runs"), "R": ("R", "i", "Runs scored (FanGraphs)"), "RBI": ("RBI*", "i", "Runs batted in (from pitch data)"),
    "BB": ("BB", "i", "Walks"), "IBB": ("IBB", "i", "Intentional walks"), "SO": ("SO", "i", "Strikeouts"),
    "HBP": ("HBP", "i", "Hit by pitch"), "SF": ("SF", "i", "Sacrifice flies"), "SH": ("SH", "i", "Sacrifice bunts"),
    "GDP": ("GDP", "i", "Grounded into double play"), "SB": ("SB", "i", "Stolen bases (FanGraphs)"), "CS": ("CS", "i", "Caught stealing (FanGraphs)"),
    "AVG": ("AVG", "r3", "Batting average"),
    # --- advanced batting
    "BB%": ("BB%", "p1", "Walk rate"), "K%": ("K%", "p1", "Strikeout rate"), "BB/K": ("BB/K", "f2", "Walks per strikeout"),
    "OBP": ("OBP", "r3", "On-base percentage"), "SLG": ("SLG", "r3", "Slugging percentage"), "OPS": ("OPS", "r3", "OBP + SLG"),
    "ISO": ("ISO", "r3", "Isolated power (SLG - AVG)"), "BABIP": ("BABIP", "r3", "Batting average on balls in play"),
    "wOBA": ("wOBA", "r3", "Weighted on-base average (Statcast weights)"), "wRC+": ("wRC+", "f0", "Weighted runs created plus, 100 = average (FanGraphs, park-adjusted)"),
    "wRAA": ("wRAA", "f1", "Runs above average from wOBA"),
    # --- batted ball
    "GB/FB": ("GB/FB", "f2", "Ground balls per fly ball"), "LD%": ("LD%", "p1", "Line-drive rate"),
    "GB%": ("GB%", "p1", "Ground-ball rate"), "FB%": ("FB%", "p1", "Fly-ball rate (incl. popups)"),
    "IFFB%": ("IFFB%", "p1", "Infield fly balls / fly balls"), "HR/FB": ("HR/FB", "p1", "Home runs per fly ball"),
    "Pull%": ("Pull%", "p1", "Pulled batted balls"), "Cent%": ("Cent%", "p1", "Batted balls up the middle"), "Oppo%": ("Oppo%", "p1", "Opposite-field batted balls"),
    # --- plate discipline
    "O-Swing%": ("O-Swing%", "p1", "Swings at pitches outside the zone (Chase%)"), "Z-Swing%": ("Z-Swing%", "p1", "Swings at pitches in the zone"),
    "Swing%": ("Swing%", "p1", "Swing rate"), "O-Contact%": ("O-Contact%", "p1", "Contact on swings outside the zone"),
    "Z-Contact%": ("Z-Contact%", "p1", "Contact on swings in the zone"), "Contact%": ("Contact%", "p1", "Contact on all swings"),
    "Zone%": ("Zone%", "p1", "Pitches in the strike zone"), "F-Strike%": ("F-Strike%", "p1", "First-pitch strikes"),
    "SwStr%": ("SwStr%", "p1", "Swinging strikes / pitches"), "CStr%": ("CStr%", "p1", "Called strikes / pitches"),
    "CSW%": ("CSW%", "p1", "Called + swinging strikes / pitches"), "Whiff%": ("Whiff%", "p1", "Misses / swings (Savant)"),
    # --- statcast
    "BBE": ("BBE", "i", "Batted-ball events"), "EV": ("EV", "f1", "Average exit velocity (mph)"), "maxEV": ("maxEV", "f1", "Max exit velocity"),
    "LA": ("LA", "f1", "Average launch angle"), "Barrels": ("Barrels", "i", "Barrels"), "Barrel%": ("Barrel%", "p1", "Barrels / batted-ball events"),
    "HardHit": ("HardHit", "i", "Batted balls 95+ mph"), "HardHit%": ("HardHit%", "p1", "Batted balls 95+ mph / BBE"),
    "SweetSpot%": ("LA Sweet-Spot%", "p1", "Launch angle 8-32 degrees"), "xBA": ("xBA", "r3", "Expected batting average"),
    "xSLG": ("xSLG", "r3", "Expected slugging"), "xwOBA": ("xwOBA", "r3", "Expected wOBA"),
    "wOBAcon": ("wOBAcon", "r3", "wOBA on contact"), "xwOBAcon": ("xwOBAcon", "r3", "Expected wOBA on contact"),
    "wOBA-xwOBA": ("wOBA-xwOBA", "r3", "Actual minus expected wOBA"),
    # --- value
    "WPA": ("WPA", "wpa", "Win probability added"), "RE24": ("RE24", "f1", "Run expectancy added"),
    "Off": ("Off", "f1", "Offensive runs above average (FanGraphs)"), "Def": ("Def", "f1", "Defensive runs (FanGraphs)"),
    "BsR": ("BsR", "f1", "Baserunning runs (FanGraphs)"), "WAR": ("WAR", "f1", "Wins above replacement (FanGraphs)"),
    # --- pitching
    "W": ("W*", "i", "Wins (from pitch data)"), "L": ("L*", "i", "Losses (from pitch data)"), "SV": ("SV*", "i", "Saves (from pitch data)"),
    "GS": ("GS", "i", "Games started"), "IP": ("IP", "ip", "Innings pitched"), "TBF": ("TBF", "i", "Batters faced"),
    "RA": ("R*", "i", "Runs allowed (charged to the pitcher on the mound)"), "ER": ("ER", "i", "Earned runs (FanGraphs)"),
    "ERA": ("ERA", "f2", "Earned run average (FanGraphs)"), "RA9": ("RA9*", "f2", "Runs allowed per 9"), "WHIP": ("WHIP", "f2", "Walks + hits per inning"),
    "K/9": ("K/9", "f2", "Strikeouts per 9"), "BB/9": ("BB/9", "f2", "Walks per 9"), "HR/9": ("HR/9", "f2", "Home runs per 9"),
    "K/BB": ("K/BB", "f2", "Strikeouts per walk"), "K-BB%": ("K-BB%", "p1", "Strikeout rate minus walk rate"),
    "FIP": ("FIP", "f2", "Fielding independent pitching (FanGraphs)"), "xFIP": ("xFIP", "f2", "Expected FIP (FanGraphs)"),
    "SIERA": ("SIERA", "f2", "Skill-interactive ERA (FanGraphs)"), "ERA-": ("ERA-", "f0", "ERA vs league, 100 = average (FanGraphs)"),
    "FIP-": ("FIP-", "f0", "FIP vs league (FanGraphs)"), "LOB%": ("LOB%", "p1", "Left-on-base rate (FanGraphs)"),
    "FBv": ("FBv", "f1", "Fastball velocity (mph)"), "Spin": ("Spin", "f0", "Average spin rate (rpm)"), "Ext": ("Ext", "f1", "Release extension (ft)"),
    "Pit": ("Pitches", "i", "Pitches thrown"),
    "Age": ("Age", "i", "Age (FanGraphs)"),
}

BAT_GROUPS = {
    "Dashboard": ["G", "PA", "HR", "R", "RBI", "SB", "BB%", "K%", "ISO", "BABIP", "AVG", "OBP", "SLG", "wOBA", "xwOBA", "wRC+", "BsR", "Off", "Def", "WAR"],
    "Standard": ["G", "AB", "PA", "H", "1B", "2B", "3B", "HR", "R", "RBI", "BB", "IBB", "SO", "HBP", "SF", "SH", "GDP", "SB", "CS", "AVG"],
    "Advanced": ["PA", "BB%", "K%", "BB/K", "AVG", "OBP", "SLG", "OPS", "ISO", "BABIP", "wOBA", "wRAA", "wRC+"],
    "Batted Ball": ["BBE", "BABIP", "GB/FB", "LD%", "GB%", "FB%", "IFFB%", "HR/FB", "Pull%", "Cent%", "Oppo%"],
    "Plate Discipline": ["PA", "O-Swing%", "Z-Swing%", "Swing%", "O-Contact%", "Z-Contact%", "Contact%", "Zone%", "F-Strike%", "SwStr%", "CStr%", "CSW%", "Whiff%"],
    "Statcast": ["PA", "BBE", "EV", "maxEV", "LA", "Barrels", "Barrel%", "HardHit", "HardHit%", "SweetSpot%", "AVG", "xBA", "SLG", "xSLG", "wOBA", "xwOBA", "wOBA-xwOBA", "wOBAcon", "xwOBAcon"],
    "Value": ["PA", "wRAA", "WPA", "RE24", "Off", "Def", "BsR", "WAR"],
}
PIT_GROUPS = {
    "Dashboard": ["W", "L", "SV", "G", "GS", "IP", "K/9", "BB/9", "HR/9", "BABIP", "LOB%", "GB%", "HR/FB", "FBv", "ERA", "xwOBA", "FIP", "xFIP", "WAR"],
    "Standard": ["W", "L", "SV", "G", "GS", "IP", "TBF", "H", "RA", "ER", "HR", "BB", "IBB", "HBP", "SO", "ERA", "RA9", "WHIP"],
    "Advanced": ["IP", "K/9", "BB/9", "K/BB", "HR/9", "K%", "BB%", "K-BB%", "AVG", "WHIP", "BABIP", "LOB%", "ERA-", "FIP-", "FIP", "xFIP", "SIERA"],
    "Batted Ball": ["BBE", "BABIP", "GB/FB", "LD%", "GB%", "FB%", "IFFB%", "HR/FB", "Pull%", "Cent%", "Oppo%"],
    "Plate Discipline": ["TBF", "O-Swing%", "Z-Swing%", "Swing%", "O-Contact%", "Z-Contact%", "Contact%", "Zone%", "F-Strike%", "SwStr%", "CStr%", "CSW%", "Whiff%"],
    "Statcast": ["TBF", "Pit", "FBv", "Spin", "Ext", "BBE", "EV", "maxEV", "LA", "Barrel%", "HardHit%", "xBA", "xSLG", "wOBA", "xwOBA", "wOBA-xwOBA"],
    "Value": ["IP", "WPA", "RE24", "WAR"],
}
GROUP_ORDER = ["Dashboard", "Standard", "Advanced", "Batted Ball", "Plate Discipline", "Statcast", "Value"]

# Savant-style percentile bars: (key, label, higher_is_better)
BAT_PCTL = [("xwOBA", "xwOBA", True), ("xBA", "xBA", True), ("xSLG", "xSLG", True), ("EV", "Avg Exit Velocity", True),
            ("Barrel%", "Barrel %", True), ("HardHit%", "Hard-Hit %", True), ("SweetSpot%", "LA Sweet-Spot %", True),
            ("O-Swing%", "Chase %", False), ("Whiff%", "Whiff %", False), ("K%", "K %", False), ("BB%", "BB %", True)]
PIT_PCTL = [("xwOBA", "xwOBA", False), ("xBA", "xBA", False), ("xSLG", "xSLG", False), ("EV", "Avg Exit Velocity", False),
            ("Barrel%", "Barrel %", False), ("HardHit%", "Hard-Hit %", False), ("O-Swing%", "Chase %", True),
            ("Whiff%", "Whiff %", True), ("K%", "K %", True), ("BB%", "BB %", False), ("GB%", "GB %", True), ("FBv", "Fastball Velo", True)]

FG_BAT_KEEP = ["WAR", "wRC+", "Off", "Def", "BsR", "R", "SB", "CS", "Age"]
FG_PIT_KEEP = ["WAR", "ERA", "FIP", "xFIP", "SIERA", "ERA-", "FIP-", "LOB%", "ER", "Age"]

# Approximate FanGraphs Guts wOBA scale, used only when FanGraphs wRC+ isn't available.
WOBA_SCALE = {2015: 1.25, 2016: 1.21, 2017: 1.19, 2018: 1.23, 2019: 1.16, 2020: 1.19, 2021: 1.21,
              2022: 1.26, 2023: 1.20, 2024: 1.24, 2025: 1.23}
FASTBALLS = {"FF", "SI", "FA"}
PITCH_NAMES = {"FF": "4-Seam Fastball", "SI": "Sinker", "FC": "Cutter", "SL": "Slider", "ST": "Sweeper", "CU": "Curveball",
               "KC": "Knuckle Curve", "SV": "Slurve", "CH": "Changeup", "FS": "Splitter", "FO": "Forkball", "SC": "Screwball",
               "KN": "Knuckleball", "EP": "Eephus", "FA": "Fastball", "CS": "Slow Curve"}


# ======================================================================= per-pitch counters
def _counters(d: pd.DataFrame) -> pd.DataFrame:
    """Add c_* counter columns to a prepped frame. Every stat is a sum of these."""
    e, pa = d["ev"], d["is_pa"]
    ind = lambda *names: (pa & e.isin(names)).astype(np.int32)  # noqa: E731
    c = {}
    c["PA"] = pa.astype(np.int32)
    c["1B"], c["2B"], c["3B"], c["HR"] = ind("single"), ind("double"), ind("triple"), ind("home_run")
    c["BB"], c["IBB"], c["HBP"] = ind("walk", "intent_walk"), ind("intent_walk"), ind("hit_by_pitch")
    c["SO"] = ind("strikeout", "strikeout_double_play")
    c["SF"], c["SH"] = ind("sac_fly", "sac_fly_double_play"), ind("sac_bunt", "sac_bunt_double_play")
    c["CI"], c["TR"], c["GDP"] = ind("catcher_interf"), ind("truncated_pa"), ind("grounded_into_double_play")
    c["RBI"] = np.where(pa & ~e.isin(["field_error", "grounded_into_double_play"]), d["runs"], 0)
    wv = d["woba_value"].fillna(0) if "woba_value" in d else 0
    c["wnum"] = np.where(pa, wv, 0)
    c["wden"] = np.where(pa, d["woba_denom"].fillna(0), 0) if "woba_denom" in d else 0
    c["xwnum"] = np.where(pa, d["xwoba_num"], 0)
    bbe = d["bbe"]
    c["BBE"] = bbe.astype(np.int32)
    c["xba"] = d["xba_num"]
    c["xslg"] = d["xslg_num"] if "xslg_num" in d else 0.0
    ls, la = d["launch_speed"], d["launch_angle"]
    c["EVs"] = np.where(bbe, ls.fillna(0), 0)
    c["LAs"] = np.where(bbe & la.notna(), la.fillna(0), 0)
    c["LAn"] = (bbe & la.notna()).astype(np.int32)
    c["HH"], c["BRL"] = d["hardhit"].astype(np.int32), d["barrel"].astype(np.int32)
    c["SS"] = (bbe & la.between(8, 32)).astype(np.int32)
    c["wcon"] = np.where(bbe, wv, 0)
    ew = d["estimated_woba_using_speedangle"].fillna(0) if "estimated_woba_using_speedangle" in d else 0
    c["xwcon"] = np.where(bbe, ew, 0)
    bt = d["bb_type"] if "bb_type" in d else pd.Series("", index=d.index)
    inplay = pa & d["type"].eq("X")
    for k, v in (("GBc", "ground_ball"), ("LDc", "line_drive"), ("FBc", "fly_ball"), ("PUc", "popup")):
        c[k] = (inplay & bt.eq(v)).astype(np.int32)
    c["HRfb"] = ((pa & e.eq("home_run")) & bt.isin(["fly_ball", "line_drive", "popup"])).astype(np.int32)
    if "hc_x" in d and "hc_y" in d:
        has = bbe & d["hc_x"].notna() & d["hc_y"].notna()
        ang = np.degrees(np.arctan2(d["hc_x"] - 125.42, 198.27 - d["hc_y"]))
        rhb = d["stand"].eq("R") if "stand" in d else True
        pull = has & np.where(rhb, ang < -15, ang > 15)
        oppo = has & np.where(rhb, ang > 15, ang < -15)
        c["SPR"], c["PULL"], c["OPPO"] = has.astype(np.int32), pull.astype(np.int32), oppo.astype(np.int32)
    else:
        c["SPR"] = c["PULL"] = c["OPPO"] = 0
    desc = d["description"].fillna("")
    sw, wh = d["swing"], d["whiff"]
    z = pd.to_numeric(d["zone"], errors="coerce") if "zone" in d else pd.Series(np.nan, index=d.index)
    known, inz = z.notna(), z.between(1, 9)
    outz = known & ~inz
    c["P"] = 1
    c["Kn"], c["Zn"], c["Ok"] = known.astype(np.int32), inz.astype(np.int32), outz.astype(np.int32)
    c["Sw"], c["ZSw"], c["OSw"] = sw.astype(np.int32), (sw & inz).astype(np.int32), (sw & outz).astype(np.int32)
    ct = sw & ~wh
    c["Ct"], c["ZCt"], c["OCt"] = ct.astype(np.int32), (ct & inz).astype(np.int32), (ct & outz).astype(np.int32)
    c["Wh"], c["CS"] = wh.astype(np.int32), desc.eq("called_strike").astype(np.int32)
    first = d["pitch_number"].eq(1)
    c["FP"], c["FS"] = first.astype(np.int32), (first & d["type"].isin(["S", "X"])).astype(np.int32)
    c["WPA"], c["RE"] = d["wpa_bat"], d["re_bat"]
    c["OUT"], c["RUN"] = d["outs_rec"], d["runs"]
    c["STR"] = d["type"].isin(["S", "X"]).astype(np.int32)
    fbv = d["fb_velo"]
    c["FBVs"], c["FBVn"] = fbv.fillna(0), fbv.notna().astype(np.int32)
    spin = pd.to_numeric(d["release_spin_rate"], errors="coerce") if "release_spin_rate" in d else pd.Series(np.nan, index=d.index)
    c["SPs"], c["SPn"] = spin.fillna(0), spin.notna().astype(np.int32)
    ext = pd.to_numeric(d["release_extension"], errors="coerce") if "release_extension" in d else pd.Series(np.nan, index=d.index)
    c["EXs"], c["EXn"] = ext.fillna(0), ext.notna().astype(np.int32)
    cdf = pd.DataFrame({f"c_{k}": (v if not np.isscalar(v) else np.full(len(d), v)) for k, v in c.items()}, index=d.index)
    cdf["c_EVmax"] = np.where(bbe, ls, np.nan)
    return cdf


SUMS = None  # filled on first use


def _sum(d: pd.DataFrame, cdf: pd.DataFrame, keys) -> pd.DataFrame:
    cols = [c for c in cdf.columns if c != "c_EVmax"]
    extra = [] if "game_pk" in keys else ["game_pk"]
    g = pd.concat([d[keys + extra], cdf], axis=1).groupby(keys, sort=False)
    t = g[cols].sum()
    t["c_EVmax"] = g["c_EVmax"].max()
    t["G"] = g["game_pk"].nunique() if extra else 1
    return t


def _div(a, b):
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(b != 0, a / b, np.nan)


def _rates(t: pd.DataFrame, side: str, lg: dict | None = None) -> pd.DataFrame:
    """Counter sums -> FanGraphs / Savant stat columns."""
    o = pd.DataFrame(index=t.index)
    C = lambda k: t[f"c_{k}"]  # noqa: E731
    o["G"] = t["G"]
    PA = C("PA")
    H = C("1B") + C("2B") + C("3B") + C("HR")
    AB = PA - C("BB") - C("HBP") - C("SF") - C("SH") - C("CI") - C("TR")
    TB = C("1B") + 2 * C("2B") + 3 * C("3B") + 4 * C("HR")
    for k in ("1B", "2B", "3B", "HR", "BB", "IBB", "SO", "HBP", "SF", "SH", "GDP"):
        o[k] = C(k)
    o["PA"], o["AB"], o["H"] = PA, AB, H
    o["AVG"], o["SLG"] = _div(H, AB), _div(TB, AB)
    o["OBP"] = _div(H + C("BB") + C("HBP"), AB + C("BB") + C("HBP") + C("SF"))
    o["OPS"], o["ISO"] = o["OBP"] + o["SLG"], o["SLG"] - o["AVG"]
    o["BABIP"] = _div(H - C("HR"), AB - C("SO") - C("HR") + C("SF"))
    o["BB%"], o["K%"] = _div(C("BB"), PA), _div(C("SO"), PA)
    o["BB/K"] = _div(C("BB"), C("SO"))
    o["K-BB%"] = o["K%"] - o["BB%"]
    o["wOBA"], o["xwOBA"] = _div(C("wnum"), C("wden")), _div(C("xwnum"), C("wden"))
    o["wOBA-xwOBA"] = o["wOBA"] - o["xwOBA"]
    o["xBA"], o["xSLG"] = _div(C("xba"), AB), _div(C("xslg"), AB)
    BBE = C("BBE")
    o["BBE"], o["EV"], o["maxEV"] = BBE, _div(C("EVs"), BBE), t["c_EVmax"]
    o["LA"] = _div(C("LAs"), C("LAn"))
    o["Barrels"], o["HardHit"] = C("BRL"), C("HH")
    o["Barrel%"], o["HardHit%"], o["SweetSpot%"] = _div(C("BRL"), BBE), _div(C("HH"), BBE), _div(C("SS"), BBE)
    o["wOBAcon"], o["xwOBAcon"] = _div(C("wcon"), BBE), _div(C("xwcon"), BBE)
    BIP = C("GBc") + C("LDc") + C("FBc") + C("PUc")
    FB = C("FBc") + C("PUc")
    o["GB%"], o["LD%"], o["FB%"] = _div(C("GBc"), BIP), _div(C("LDc"), BIP), _div(FB, BIP)
    o["IFFB%"], o["HR/FB"], o["GB/FB"] = _div(C("PUc"), FB), _div(C("HR"), FB), _div(C("GBc"), FB)
    o["Pull%"], o["Oppo%"] = _div(C("PULL"), C("SPR")), _div(C("OPPO"), C("SPR"))
    o["Cent%"] = 1 - o["Pull%"] - o["Oppo%"]
    o["O-Swing%"], o["Z-Swing%"], o["Swing%"] = _div(C("OSw"), C("Ok")), _div(C("ZSw"), C("Zn")), _div(C("Sw"), C("P"))
    o["O-Contact%"], o["Z-Contact%"], o["Contact%"] = _div(C("OCt"), C("OSw")), _div(C("ZCt"), C("ZSw")), _div(C("Ct"), C("Sw"))
    o["Zone%"], o["F-Strike%"] = _div(C("Zn"), C("Kn")), _div(C("FS"), C("FP"))
    o["SwStr%"], o["CStr%"] = _div(C("Wh"), C("P")), _div(C("CS"), C("P"))
    o["CSW%"], o["Whiff%"] = o["SwStr%"] + o["CStr%"], _div(C("Wh"), C("Sw"))
    o["Pit"] = C("P")
    if side == "bat":
        o["RBI"] = C("RBI")
        o["WPA"], o["RE24"] = C("WPA"), C("RE")
        if lg:
            o["wRAA"] = (o["wOBA"] - lg["wOBA"]) / lg["scale"] * PA
            rpa = lg["R/PA"]
            o["wRC+_calc"] = 100 * ((o["wRAA"] / PA.replace(0, np.nan)) + rpa) / rpa
    else:
        OUT = C("OUT")
        o["TBF"], o["Outs"], o["RA"] = PA, OUT, C("RUN")
        o["IP"] = OUT  # stored as outs, shown as 123.1
        o["RA9"] = _div(C("RUN") * 27, OUT)
        o["WHIP"] = _div((H + C("BB")) * 3, OUT)
        o["K/9"], o["BB/9"], o["HR/9"] = _div(C("SO") * 27, OUT), _div(C("BB") * 27, OUT), _div(C("HR") * 27, OUT)
        o["K/BB"] = _div(C("SO"), C("BB"))
        o["FBv"], o["Spin"], o["Ext"] = _div(C("FBVs"), C("FBVn")), _div(C("SPs"), C("SPn")), _div(C("EXs"), C("EXn"))
        o["WPA"], o["RE24"] = -C("WPA"), -C("RE")
        if lg:
            o["FIP_calc"] = _div(13 * C("HR") + 3 * (C("BB") - C("IBB") + C("HBP")) - 2 * C("SO"), OUT / 3) + lg["cFIP"]
    return o


# ======================================================================= helpers
def _names() -> dict:
    try:
        return data.get_names()
    except Exception:
        return {}


def _first_list(series: pd.Series):
    return series.groupby(level=0).agg(lambda s: list(dict.fromkeys(s)))


def _team_label(teams: list) -> str:
    return teams[0] if len(teams) == 1 else f"{len(teams)} Tms"


def _fg_merge(df: pd.DataFrame, fg: pd.DataFrame, keep: list) -> pd.DataFrame:
    for k in keep:
        df[f"fg_{k}"] = np.nan
    if fg is None or fg.empty or "mlbam" not in fg:
        return df
    f = fg.drop_duplicates("mlbam").set_index("mlbam")
    for k in keep:
        if k in f.columns:
            df[f"fg_{k}"] = df.index.map(f[k])
    if "fg_name" in f.columns:
        df["fg_name"] = df.index.map(f["fg_name"])
    return df


def _percentiles(df: pd.DataFrame, qual: pd.Series, spec) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)
    pool = df[qual]
    for key, _, higher in spec:
        if key not in df or pool[key].notna().sum() < 5:
            out[key] = np.nan
            continue
        vals = np.sort(pool[key].dropna().to_numpy())
        x = df[key].to_numpy(dtype=float)
        rank = np.searchsorted(vals, x, side="right") / len(vals)
        p = np.clip(np.round(rank * 100), 1, 100) if higher else np.clip(np.round((1 - rank) * 100) + 1, 1, 100)
        p = np.where(np.isnan(x), np.nan, p)
        out[key] = p
    return out


# ======================================================================= season
@lru_cache(maxsize=None)
def season(year: int, kind: str = "R") -> dict | None:
    """All stats for one season. kind 'R' = regular season, 'P' = postseason."""
    raw = data.get_regular_season(year) if kind == "R" else data.get_postseason(year)
    if raw is None or raw.empty:
        return None
    if kind == "P":
        raw = raw[raw["game_type"].isin(POST)]
    else:
        raw = raw[raw["game_type"] == "R"]
    if raw.empty:
        return None
    d = S.prep(raw).reset_index(drop=True)
    cdf = _counters(d)
    names = _names()

    # league baselines
    tot = cdf.sum()
    PA_all = tot["c_PA"]
    lg = {"wOBA": tot["c_wnum"] / tot["c_wden"] if tot["c_wden"] else 0.315,
          "scale": WOBA_SCALE.get(year, 1.22), "R/PA": d["runs"].sum() / PA_all if PA_all else 0.12}
    outs = tot["c_OUT"]
    lgRA9 = d["runs"].sum() * 27 / outs if outs else 4.4
    lg["cFIP"] = lgRA9 * 0.92 - (13 * tot["c_HR"] + 3 * (tot["c_BB"] - tot["c_IBB"] + tot["c_HBP"]) - 2 * tot["c_SO"]) / (outs / 3) if outs else 3.1

    gt = S.games_table(d)
    team_games = int(pd.concat([gt["home"], gt["away"]]).value_counts().max()) if len(gt) else 0

    # ---- batters
    bt = _sum(d, cdf, ["batter"])
    bat = _rates(bt, "bat", lg)
    teams_b = d.groupby("batter", sort=False)["bat_team"].agg(lambda s: list(dict.fromkeys(s)))
    bat["teams"] = teams_b
    bat["Team"] = bat["teams"].map(_team_label)
    bat["stand"] = d.groupby("batter")["stand"].agg(lambda s: "S" if s.nunique() > 1 else s.iloc[0]) if "stand" in d else ""
    bat = _fg_merge(bat, _fg(year, "bat") if kind == "R" else None, FG_BAT_KEEP).copy()
    bat["wRC+"] = bat["fg_wRC+"].where(bat["fg_wRC+"].notna(), bat.get("wRC+_calc"))
    for k in ("WAR", "Off", "Def", "BsR", "R", "SB", "CS", "Age"):
        bat[k] = bat[f"fg_{k}"]
    pos = S.primary_positions(d)
    bat["Pos"] = bat.index.map(lambda p: pos.get(int(p), "DH"))
    bat["Name"] = [names.get(int(i)) or (bat.at[i, "fg_name"] if "fg_name" in bat and isinstance(bat.at[i, "fg_name"], str) else f"#{i}") for i in bat.index]
    bat["q"] = bat["PA"] >= 3.1 * team_games if kind == "R" else bat["PA"] >= 1
    bat_pct = _percentiles(bat, bat["q"], BAT_PCTL) if kind == "R" else None

    # ---- pitchers
    pt = _sum(d, cdf, ["pitcher"])
    pit = _rates(pt, "pit", lg)
    pit["teams"] = d.groupby("pitcher", sort=False)["fld_team"].agg(lambda s: list(dict.fromkeys(s)))
    pit["Team"] = pit["teams"].map(_team_label)
    pit["throws"] = d.groupby("pitcher")["p_throws"].first() if "p_throws" in d else ""
    st = S.starters(d)
    pit["GS"] = st["pitcher"].value_counts().reindex(pit.index).fillna(0).astype(int)
    # one pass over every game for W / L / SV
    dec_g = {}       # (pitcher, game) -> 'W' | 'L' | 'SV'
    for pk, g in d.groupby("game_pk", sort=False):
        for k, p in S.decisions(g).items():
            if p:
                dec_g[(int(p), int(pk))] = k
    pfirst_team = d.groupby(["pitcher", "game_pk"])["fld_team"].first()
    dec = {"W": {}, "L": {}, "SV": {}}
    dec_t = {}
    for (p, pk), k in dec_g.items():
        dec[k][p] = dec[k].get(p, 0) + 1
        team = pfirst_team.get((p, pk))
        dec_t[(p, team, k)] = dec_t.get((p, team, k), 0) + 1
    for k in ("W", "L", "SV"):
        pit[k] = pit.index.map(lambda p, k=k: dec[k].get(int(p), 0)).astype(int)
    pit = _fg_merge(pit, _fg(year, "pit") if kind == "R" else None, FG_PIT_KEEP).copy()
    pit["FIP"] = pit["fg_FIP"].where(pit["fg_FIP"].notna(), pit.get("FIP_calc"))
    for k in ("WAR", "ERA", "xFIP", "SIERA", "ERA-", "FIP-", "LOB%", "ER", "Age"):
        pit[k] = pit[f"fg_{k}"]
    if pit["LOB%"].notna().any() and pit["LOB%"].max() > 1.5:
        pit["LOB%"] = pit["LOB%"] / 100
    raw_names = d.drop_duplicates("pitcher").set_index("pitcher")["player_name"] if "player_name" in d else pd.Series(dtype=object)

    def pname(i):
        n = names.get(int(i))
        if n:
            return n
        r = raw_names.get(i)
        if isinstance(r, str) and "," in r:
            return " ".join(reversed([x.strip() for x in r.split(",")]))
        return r if isinstance(r, str) else f"#{i}"
    pit["Name"] = [pname(i) for i in pit.index]
    pit["q"] = pit["Outs"] >= 3 * team_games if kind == "R" else pit["TBF"] >= 1
    pit["role"] = np.where(pit["GS"] >= pit["G"] / 2, "SP", "RP")
    pit_pool = pit["TBF"] >= 1.25 * team_games
    pit_pct = _percentiles(pit, pit_pool, PIT_PCTL) if kind == "R" else None

    # ---- by team (team pages)
    bteam = _rates(_sum(d, cdf, ["batter", "bat_team"]), "bat", lg)
    pteam = _rates(_sum(d, cdf, ["pitcher", "fld_team"]), "pit", lg)
    gs_t = st.groupby(["pitcher", "fld_team"]).size()
    pteam["GS"] = pteam.index.map(lambda ix: int(gs_t.get(ix, 0)))
    for k in ("W", "L", "SV"):
        pteam[k] = 0
    for (p, team, k), n in dec_t.items():
        if (p, team) in pteam.index:
            pteam.loc[(p, team), k] = n
    team_bat = _rates(_sum(d, cdf, ["bat_team"]), "bat", lg)
    team_pit = _rates(_sum(d, cdf, ["fld_team"]), "pit", lg)

    # ---- game logs
    bg = _rates(_sum(d, cdf, ["batter", "game_pk"]), "bat", lg)
    pg = _rates(_sum(d, cdf, ["pitcher", "game_pk"]), "pit", lg)
    ginfo = gt.set_index("game_pk")
    side_b = d.groupby(["batter", "game_pk"])[["bat_team", "fld_team", "bat_home"]].first()
    bg = bg.join(side_b)
    side_p = d.groupby(["pitcher", "game_pk"])[["fld_team", "bat_team", "bat_home"]].first()
    pg = pg.join(side_p)
    st_set = set(zip(st["pitcher"].astype(int), st["game_pk"].astype(int)))
    pg["GS"] = [int((int(p), int(g)) in st_set) for p, g in pg.index]
    pg["Dec"] = [dec_g.get((int(p), int(g)), "") for p, g in pg.index]
    bpos = {}
    for pk, g in d.groupby("game_pk", sort=False):
        for pid, lst in S.player_positions(g).items():
            bpos[(pid, int(pk))] = "-".join(lst)
    bg["Pos"] = [bpos.get((int(p), int(g)), "") for p, g in bg.index]

    # ---- pitch arsenal
    ars = None
    if "pitch_type" in d:
        dd = d[d["pitch_type"].notna() & (d["pitch_type"] != "")]
        if not dd.empty:
            a = _sum(dd, cdf.loc[dd.index], ["pitcher", "pitch_type"])
            ar = pd.DataFrame(index=a.index)
            ar["N"] = a["c_P"]
            tot_p = a.groupby(level=0)["c_P"].transform("sum")
            ar["Usage%"] = a["c_P"] / tot_p
            gb = dd.groupby(["pitcher", "pitch_type"])
            ar["Velo"] = gb["release_speed"].mean()
            if "release_spin_rate" in dd:
                ar["Spin"] = pd.to_numeric(dd["release_spin_rate"], errors="coerce").groupby([dd["pitcher"], dd["pitch_type"]]).mean()
            for col, name in (("pfx_x", "HB"), ("pfx_z", "IVB")):
                if col in dd:
                    ar[name] = pd.to_numeric(dd[col], errors="coerce").groupby([dd["pitcher"], dd["pitch_type"]]).mean() * 12
            ar["Whiff%"] = _div(a["c_Wh"], a["c_Sw"])
            ar["CSW%"] = _div(a["c_Wh"] + a["c_CS"], a["c_P"])
            ar["Chase%"] = _div(a["c_OSw"], a["c_Ok"])
            ar["PA"] = a["c_PA"]
            AB = a["c_PA"] - a["c_BB"] - a["c_HBP"] - a["c_SF"] - a["c_SH"] - a["c_CI"] - a["c_TR"]
            ar["BA"] = _div(a["c_1B"] + a["c_2B"] + a["c_3B"] + a["c_HR"], AB)
            ar["xBA"] = _div(a["c_xba"], AB)
            ar["wOBA"], ar["xwOBA"] = _div(a["c_wnum"], a["c_wden"]), _div(a["c_xwnum"], a["c_wden"])
            ar["K%"] = _div(a["c_SO"], a["c_PA"])
            ar["EV"] = _div(a["c_EVs"], a["c_BBE"])
            ar["HardHit%"] = _div(a["c_HH"], a["c_BBE"])
            ar["RV"] = -a["c_RE"]
            ar["RV/100"] = ar["RV"] / ar["N"] * 100
            ars = ar

    # ---- spray (batted balls for spray charts)
    spray = None
    if "hc_x" in d:
        sp = d.loc[d["bbe"] & d["hc_x"].notna(), ["batter", "pitcher", "hc_x", "hc_y", "ev", "launch_speed"]]
        spray = sp

    # ---- standings
    standings = _standings(gt, year) if kind == "R" else None

    all_t = _sum(d.assign(_all=1), cdf, ["_all"])
    return dict(year=year, kind=kind, d=d, bat=bat, pit=pit, bat_pct=bat_pct, pit_pct=pit_pct,
                bteam=bteam, pteam=pteam, team_bat=team_bat, team_pit=team_pit, bg=bg, pg=pg, ginfo=ginfo,
                arsenal=ars, spray=spray, standings=standings, games=gt, lg=lg, team_games=team_games,
                lg_bat=_rates(all_t, "bat", lg).iloc[0].to_dict(), lg_pit=_rates(all_t, "pit", lg).iloc[0].to_dict())


def _fg(year, kind):
    try:
        return data.get_fangraphs(kind, year)
    except Exception:
        return pd.DataFrame()


def _standings(gt: pd.DataFrame, year: int) -> list[dict]:
    rows = {}
    g = gt.sort_values(["date", "game_pk"])
    for r in g.itertuples():
        if not r.winner:
            continue
        for team, rs, ra, home in ((r.home, r.home_r, r.away_r, True), (r.away, r.away_r, r.home_r, False)):
            t = rows.setdefault(team, dict(team=team, W=0, L=0, RS=0, RA=0, hW=0, hL=0, aW=0, aL=0, res=[]))
            win = team == r.winner
            t["W" if win else "L"] += 1
            t["RS"] += rs
            t["RA"] += ra
            t[("h" if home else "a") + ("W" if win else "L")] += 1
            t["res"].append("W" if win else "L")
    out = {}
    for t in rows.values():
        if t["team"] not in TEAMS:
            continue
        G = t["W"] + t["L"]
        t["pct"] = t["W"] / G if G else 0
        rs, ra = t["RS"], t["RA"]
        t["pyth"] = rs ** 1.83 / (rs ** 1.83 + ra ** 1.83) if rs + ra else 0.5
        t["xW"] = round(t["pyth"] * G)
        t["xL"] = G - t["xW"]
        last = t["res"][-10:]
        t["L10"] = f"{last.count('W')}-{last.count('L')}"
        n, cur = 0, t["res"][-1] if t["res"] else ""
        for x in reversed(t["res"]):
            if x != cur:
                break
            n += 1
        t["Strk"] = f"{cur}{n}" if cur else ""
        t["name"] = team_name(t["team"], year)
        key = f"{league_of(t['team'], year)} {division(t['team'], year)}"
        out.setdefault(key, []).append(t)
    res = []
    for key in sorted(out):
        teams = sorted(out[key], key=lambda t: (-t["pct"], -t["W"]))
        lead = teams[0]
        for t in teams:
            gb = ((lead["W"] - t["W"]) + (t["L"] - lead["L"])) / 2
            t["GB"] = "—" if gb == 0 else (f"{gb:.1f}".rstrip("0").rstrip("."))
        res.append(dict(div=key, teams=teams))
    return res


# ======================================================================= player lookup across seasons
def player_seasons(pid: int, years_r, years_p) -> dict:
    """Every saved season of one player, regular season and postseason."""
    out = {"bat": [], "pit": [], "bat_post": [], "pit_post": []}
    for y in sorted(years_r):
        s = season(y, "R")
        if not s:
            continue
        for side in ("bat", "pit"):
            df = s[side]
            if pid in df.index:
                r = df.loc[pid].to_dict()
                r["season"] = y
                out[side].append(r)
    for y in sorted(years_p):
        s = season(y, "P")
        if not s:
            continue
        for side in ("bat", "pit"):
            df = s[side]
            if pid in df.index:
                r = df.loc[pid].to_dict()
                r["season"] = y
                out[f"{side}_post"].append(r)
    return out


def total_row(rows: list[dict], side: str) -> dict | None:
    """Career totals: recompute rates from summed counting stats."""
    if not rows:
        return None
    t = {}
    sums = ["G", "PA", "AB", "H", "1B", "2B", "3B", "HR", "RBI", "BB", "IBB", "SO", "HBP", "SF", "SH", "GDP", "BBE",
            "Barrels", "HardHit", "Pit", "WPA", "RE24", "R", "SB", "CS", "WAR", "Off", "Def", "BsR", "wRAA",
            "W", "L", "SV", "GS", "TBF", "Outs", "RA", "ER"]
    for k in sums:
        vals = [r.get(k) for r in rows if r.get(k) is not None and not (isinstance(r.get(k), float) and math.isnan(r.get(k)))]
        t[k] = sum(vals) if vals else NAN
    w = "PA" if side == "bat" else "TBF"

    def wavg(k, wk=w):
        pairs = [(r.get(k), r.get(wk) or 0) for r in rows]
        pairs = [(v, n) for v, n in pairs if v is not None and not (isinstance(v, float) and math.isnan(v)) and n]
        tot = sum(n for _, n in pairs)
        return sum(v * n for v, n in pairs) / tot if tot else NAN
    PA, AB, H = t["PA"], t["AB"], t["H"]
    sd = lambda a, b: a / b if b else NAN  # noqa: E731
    t["AVG"] = sd(H, AB)
    TB = t["1B"] + 2 * t["2B"] + 3 * t["3B"] + 4 * t["HR"] if not math.isnan(t["1B"]) else NAN
    t["SLG"] = sd(TB, AB)
    t["OBP"] = sd(H + t["BB"] + t["HBP"], AB + t["BB"] + t["HBP"] + t["SF"])
    t["OPS"] = (t["OBP"] or 0) + (t["SLG"] or 0)
    t["ISO"] = t["SLG"] - t["AVG"]
    t["BABIP"] = sd(H - t["HR"], AB - t["SO"] - t["HR"] + t["SF"])
    t["BB%"], t["K%"] = sd(t["BB"], PA), sd(t["SO"], PA)
    t["BB/K"], t["K-BB%"] = sd(t["BB"], t["SO"]), t["K%"] - t["BB%"]
    for k in ("wOBA", "xwOBA", "xBA", "xSLG", "wRC+", "GB%", "LD%", "FB%", "IFFB%", "HR/FB", "GB/FB", "Pull%", "Cent%",
              "Oppo%", "O-Swing%", "Z-Swing%", "Swing%", "O-Contact%", "Z-Contact%", "Contact%", "Zone%", "F-Strike%",
              "SwStr%", "CStr%", "CSW%", "Whiff%", "wOBAcon", "xwOBAcon", "LA", "SweetSpot%", "FBv", "Spin", "Ext",
              "ERA-", "FIP-", "LOB%"):
        t[k] = wavg(k)
    t["EV"] = wavg("EV", "BBE")
    t["maxEV"] = max([r.get("maxEV") for r in rows if r.get("maxEV") == r.get("maxEV") and r.get("maxEV") is not None] or [NAN])
    t["Barrel%"], t["HardHit%"] = sd(t["Barrels"], t["BBE"]), sd(t["HardHit"], t["BBE"])
    t["wOBA-xwOBA"] = t["wOBA"] - t["xwOBA"]
    if side == "pit":
        outs = t["Outs"]
        t["IP"] = outs
        t["RA9"] = sd(t["RA"] * 27, outs)
        t["ERA"] = sd(t["ER"] * 27, outs) if t["ER"] == t["ER"] else NAN
        t["WHIP"] = sd((H + t["BB"]) * 3, outs)
        t["K/9"], t["BB/9"], t["HR/9"] = sd(t["SO"] * 27, outs), sd(t["BB"] * 27, outs), sd(t["HR"] * 27, outs)
        t["K/BB"] = sd(t["SO"], t["BB"])
        t["K%"], t["BB%"] = sd(t["SO"], t["TBF"]), sd(t["BB"], t["TBF"])
        t["K-BB%"] = t["K%"] - t["BB%"]
        for k in ("FIP", "xFIP", "SIERA"):
            t[k] = wavg(k, "Outs")
    return t

