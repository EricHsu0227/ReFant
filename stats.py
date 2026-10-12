"""Turn Statcast pitch-by-pitch data into box scores, series, and season tables.

Statcast doesn't record official scoring, so a few things are reconstructed:
  * runs are charged to the pitcher on the mound when they score (no inherited-runner rules)
  * RBI = runs scoring on the plate appearance, excluding errors and GIDP
  * W / L / SV are derived from the lead changes and may occasionally differ from the official call
These are marked with * on the pages.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from teams import ROUND_ORDER, league, round_name, canon

PA_EVENTS = {
    "single", "double", "triple", "home_run", "walk", "intent_walk", "hit_by_pitch",
    "strikeout", "strikeout_double_play", "field_out", "force_out", "double_play",
    "grounded_into_double_play", "triple_play", "fielders_choice", "fielders_choice_out",
    "field_error", "sac_fly", "sac_fly_double_play", "sac_bunt", "sac_bunt_double_play",
    "catcher_interf", "truncated_pa", "other_out",
}
HIT_EVENTS = {"single", "double", "triple", "home_run"}
OUTS_BY_EVENT = {
    "field_out": 1, "strikeout": 1, "force_out": 1, "sac_fly": 1, "sac_bunt": 1,
    "fielders_choice_out": 1, "other_out": 1, "double_play": 2,
    "grounded_into_double_play": 2, "strikeout_double_play": 2, "sac_fly_double_play": 2,
    "sac_bunt_double_play": 2, "triple_play": 3,
}
SWINGS = {"swinging_strike", "swinging_strike_blocked", "foul", "foul_tip", "hit_into_play",
          "foul_bunt", "missed_bunt", "bunt_foul_tip"}
WHIFFS = {"swinging_strike", "swinging_strike_blocked", "missed_bunt"}
POS = {"fielder_2": "C", "fielder_3": "1B", "fielder_4": "2B", "fielder_5": "3B",
       "fielder_6": "SS", "fielder_7": "LF", "fielder_8": "CF", "fielder_9": "RF"}
FASTBALLS = {"FF", "SI", "FA"}


def _col(df, name, default=np.nan):
    return df[name] if name in df.columns else pd.Series(default, index=df.index)


# ======================================================================= prep
def prep(df: pd.DataFrame) -> pd.DataFrame:
    """Add derived columns used everywhere. Idempotent."""
    if df is None or df.empty or "_prepped" in df.columns:
        return df
    df = df.sort_values(["game_date", "game_pk", "at_bat_number", "pitch_number"]).reset_index(drop=True).copy()
    top = df["inning_topbot"].eq("Top")
    df["bat_team"] = np.where(top, df["away_team"], df["home_team"])
    df["fld_team"] = np.where(top, df["home_team"], df["away_team"])
    df["bat_home"] = ~top
    df["runs"] = (df["post_bat_score"] - df["bat_score"]).clip(lower=0).fillna(0)
    ev = df["events"].fillna("")
    df["ev"] = ev
    df["is_pa"] = ev.isin(PA_EVENTS)

    # --- outs recorded on each pitch
    half = ["game_pk", "inning", "inning_topbot"]
    nxt = df.groupby(half)["outs_when_up"].shift(-1)
    outs = (nxt - df["outs_when_up"])
    last_in_half = nxt.isna()
    # final half-inning of each game: was it a walk-off?
    order = df["inning"] * 2 + (~top).astype(int)
    df["_order"] = order
    game_last = df.groupby("game_pk")["_order"].transform("max")
    final_home = df.groupby("game_pk")["post_home_score"].transform("max")
    final_away = df.groupby("game_pk")["post_away_score"].transform("max")
    walkoff = (order == game_last) & (~top) & (final_home > final_away)
    ev_outs = ev.map(OUTS_BY_EVENT).fillna(0)
    ev_outs = ev_outs.where(~ev.str.startswith(("caught_stealing", "pickoff_caught")), 1)
    end_outs = np.where(walkoff, np.minimum(ev_outs, 3 - df["outs_when_up"]), 3 - df["outs_when_up"])
    outs = outs.where(~last_in_half, end_outs)
    df["outs_rec"] = outs.clip(lower=0, upper=3).fillna(0).astype(int)

    # --- win probability / run value from both perspectives
    dwe = _col(df, "delta_home_win_exp", 0).fillna(0)
    df["wpa_bat"] = np.where(df["bat_home"], dwe, -dwe)
    df["re_bat"] = _col(df, "delta_run_exp", 0).fillna(0)

    # --- batted ball
    df["bbe"] = df["type"].eq("X") & _col(df, "launch_speed").notna()
    ls = _col(df, "launch_speed")
    df["hardhit"] = df["bbe"] & (ls >= 95)
    df["barrel"] = df["bbe"] & _col(df, "launch_speed_angle").eq(6)
    wv = _col(df, "woba_value", 0).fillna(0)
    xw = _col(df, "estimated_woba_using_speedangle")
    df["xwoba_num"] = np.where(df["type"].eq("X") & xw.notna(), xw, wv)
    df["xba_num"] = np.where(df["bbe"], _col(df, "estimated_ba_using_speedangle", 0).fillna(0), 0)
    if "estimated_slg_using_speedangle" in df.columns:
        df["xslg_num"] = np.where(df["bbe"], df["estimated_slg_using_speedangle"].fillna(0), 0)
    desc = df["description"].fillna("")
    df["swing"] = desc.isin(SWINGS)
    df["whiff"] = desc.isin(WHIFFS)
    df["fb_velo"] = np.where(_col(df, "pitch_type", "").isin(FASTBALLS), _col(df, "release_speed"), np.nan)
    df["_prepped"] = True
    return df


# ======================================================================= batting
def _batting_agg(g: pd.DataFrame) -> dict:
    pa = g[g["is_pa"]]
    e = pa["ev"]
    n = lambda *names: int(e.isin(names).sum())
    BB, IBB, HBP = n("walk", "intent_walk"), n("intent_walk"), n("hit_by_pitch")
    SF, SH, CI = n("sac_fly", "sac_fly_double_play"), n("sac_bunt", "sac_bunt_double_play"), n("catcher_interf")
    H1, H2, H3, HR = n("single"), n("double"), n("triple"), n("home_run")
    H = H1 + H2 + H3 + HR
    PA = len(pa)
    AB = PA - BB - HBP - SF - SH - CI - n("truncated_pa")
    SO = n("strikeout", "strikeout_double_play")
    rbi_rows = pa[~e.isin(["field_error", "grounded_into_double_play"])]
    TB = H1 + 2 * H2 + 3 * H3 + 4 * HR
    obp_den = AB + BB + HBP + SF
    wden = pa["woba_denom"].fillna(0).sum() if "woba_denom" in pa else 0
    bbe = g[g["bbe"]]
    out = dict(
        G=g["game_pk"].nunique(), PA=PA, AB=AB, H=H, **{"2B": H2, "3B": H3}, HR=HR,
        RBI=int(rbi_rows["runs"].sum()), BB=BB, IBB=IBB, SO=SO, HBP=HBP, SF=SF, SH=SH,
        GDP=n("grounded_into_double_play"), TB=TB,
        AVG=H / AB if AB else np.nan,
        OBP=(H + BB + HBP) / obp_den if obp_den else np.nan,
        SLG=TB / AB if AB else np.nan,
        wOBA=pa["woba_value"].fillna(0).sum() / wden if wden else np.nan,
        xwOBA=pa["xwoba_num"].sum() / wden if wden else np.nan,
        xBA=pa["xba_num"].sum() / AB if AB else np.nan,
        xSLG=(pa["xslg_num"].sum() / AB if AB else np.nan) if "xslg_num" in pa else np.nan,
        BBE=len(bbe),
        EV=bbe["launch_speed"].mean() if len(bbe) else np.nan,
        maxEV=bbe["launch_speed"].max() if len(bbe) else np.nan,
        LA=bbe["launch_angle"].mean() if len(bbe) else np.nan,
        HardHit=bbe["hardhit"].mean() if len(bbe) else np.nan,
        Barrel=bbe["barrel"].mean() if len(bbe) else np.nan,
        Kpct=SO / PA if PA else np.nan, BBpct=BB / PA if PA else np.nan,
        WPA=g["wpa_bat"].sum(), RE24=g["re_bat"].sum(),
        Pit=len(g),
    )
    out["OPS"] = (out["OBP"] or 0) + (out["SLG"] or 0) if PA else np.nan
    return out


def player_positions(df: pd.DataFrame) -> dict[int, list[str]]:
    """mlbam -> positions in order of first appearance (from fielder_2..9 on each pitch)."""
    cols = [c for c in POS if c in df.columns]
    if not cols:
        return {}
    long = df[["game_pk", "at_bat_number", "pitch_number"] + cols].melt(
        id_vars=["game_pk", "at_bat_number", "pitch_number"], value_name="pid", var_name="slot")
    long = long.dropna(subset=["pid"])
    long["pid"] = long["pid"].astype(int)
    long["pos"] = long["slot"].map(POS)
    out: dict[int, list[str]] = {}
    for pid, grp in long.groupby("pid", sort=False):
        out[int(pid)] = list(dict.fromkeys(grp["pos"]))
    return out


def primary_positions(df: pd.DataFrame) -> dict[int, str]:
    cols = [c for c in POS if c in df.columns]
    if not cols:
        return {}
    pa_rows = df[df["is_pa"]]
    long = pa_rows[cols].melt(value_name="pid", var_name="slot").dropna()
    long["pid"] = long["pid"].astype(int)
    counts = long.groupby(["pid", "slot"]).size().reset_index(name="n")
    top = counts.sort_values("n", ascending=False).drop_duplicates("pid")
    return {int(p): POS[s] for p, s in zip(top["pid"], top["slot"])}


_BAT_NEED = ["game_pk", "batter", "at_bat_number", "is_pa", "ev", "runs", "woba_value", "woba_denom", "xwoba_num",
             "xba_num", "xslg_num", "bbe", "launch_speed", "launch_angle", "hardhit", "barrel", "wpa_bat", "re_bat"]
_PIT_NEED = ["game_pk", "pitcher", "player_name", "is_pa", "ev", "outs_rec", "runs", "type", "woba_value", "woba_denom",
             "xwoba_num", "bbe", "launch_speed", "hardhit", "barrel", "swing", "whiff", "fb_velo", "wpa_bat", "re_bat"]


def _slim(d: pd.DataFrame, cols) -> pd.DataFrame:
    return d[[c for c in cols if c in d.columns]]


def batting_table(df: pd.DataFrame, team: str, names: dict, order: str = "lineup") -> pd.DataFrame:
    d = _slim(df[df["bat_team"] == team], _BAT_NEED)
    if d.empty:
        return pd.DataFrame()
    rows = []
    first_seen = d.groupby("batter")["at_bat_number"].min() if order == "lineup" else None
    for pid, g in d.groupby("batter", sort=False):
        r = _batting_agg(g)
        r["id"] = int(pid)
        r["Name"] = names.get(int(pid), str(pid))
        rows.append(r)
    t = pd.DataFrame(rows)
    if order == "lineup":
        t["_o"] = t["id"].map(first_seen)
        t = t.sort_values("_o").drop(columns="_o")
    else:
        t = t.sort_values("PA", ascending=False)
    return t.reset_index(drop=True)


def batting_total(df: pd.DataFrame, team: str) -> dict:
    d = df[df["bat_team"] == team]
    return _batting_agg(d) if not d.empty else {}


# ======================================================================= pitching
def _pitching_agg(g: pd.DataFrame) -> dict:
    pa = g[g["is_pa"]]
    e = pa["ev"]
    n = lambda *names: int(e.isin(names).sum())
    outs = int(g["outs_rec"].sum())
    H, BB, HBP = n(*HIT_EVENTS), n("walk", "intent_walk"), n("hit_by_pitch")
    SO, HR = n("strikeout", "strikeout_double_play"), n("home_run")
    R = int(g["runs"].sum())
    BF = len(pa)
    wden = pa["woba_denom"].fillna(0).sum() if "woba_denom" in pa else 0
    bbe = g[g["bbe"]]
    sw = int(g["swing"].sum())
    return dict(
        G=g["game_pk"].nunique(), Outs=outs, H=H, R=R, BB=BB, SO=SO, HR=HR, HBP=HBP, BF=BF,
        Pit=len(g), Str=int(g["type"].isin(["S", "X"]).sum()),
        RA9=R * 27 / outs if outs else np.nan,
        WHIP=(H + BB) * 3 / outs if outs else np.nan,
        Kpct=SO / BF if BF else np.nan, BBpct=BB / BF if BF else np.nan,
        KBB=(SO - BB) / BF if BF else np.nan,
        wOBA=pa["woba_value"].fillna(0).sum() / wden if wden else np.nan,
        xwOBA=pa["xwoba_num"].sum() / wden if wden else np.nan,
        EV=bbe["launch_speed"].mean() if len(bbe) else np.nan,
        HardHit=bbe["hardhit"].mean() if len(bbe) else np.nan,
        Barrel=bbe["barrel"].mean() if len(bbe) else np.nan,
        Whiff=g["whiff"].sum() / sw if sw else np.nan,
        Velo=g["fb_velo"].mean(),
        WPA=-g["wpa_bat"].sum(), RE24=-g["re_bat"].sum(),
    )


def starters(df: pd.DataFrame) -> pd.DataFrame:
    """First pitcher used by each fielding team in each game."""
    return df.groupby(["game_pk", "fld_team"], sort=False).first().reset_index()[["game_pk", "fld_team", "pitcher"]]


def pitching_table(df: pd.DataFrame, team: str, names: dict, order: str = "appearance",
                   decisions: dict | None = None) -> pd.DataFrame:
    d = df[df["fld_team"] == team]
    if d.empty:
        return pd.DataFrame()
    d_full = d
    d = _slim(d, _PIT_NEED)
    if d.empty:
        return pd.DataFrame()
    st = starters(df)
    st = st[st["fld_team"] == team]
    gs = st["pitcher"].value_counts()
    first_seen = d.reset_index().groupby("pitcher")["index"].min()
    rows = []
    for pid, g in d.groupby("pitcher", sort=False):
        r = _pitching_agg(g)
        pid = int(pid)
        r["id"] = pid
        r["GS"] = int(gs.get(pid, 0))
        nm = names.get(pid)
        if not nm and "player_name" in g:
            raw = str(g["player_name"].iloc[0])
            nm = " ".join(reversed([p.strip() for p in raw.split(",")])) if "," in raw else raw
        r["Name"] = nm or str(pid)
        if decisions:
            r["W"] = decisions.get("W", {}).get(pid, 0)
            r["L"] = decisions.get("L", {}).get(pid, 0)
            r["SV"] = decisions.get("SV", {}).get(pid, 0)
        rows.append(r)
    t = pd.DataFrame(rows)
    if order == "appearance":
        t["_o"] = t["id"].map(first_seen)
        t = t.sort_values("_o").drop(columns="_o")
    else:
        t = t.sort_values(["GS", "Outs"], ascending=False)
    return t.reset_index(drop=True)


def pitching_total(df: pd.DataFrame, team: str) -> dict:
    d = df[df["fld_team"] == team]
    return _pitching_agg(d) if not d.empty else {}


# ======================================================================= games
def games_table(df: pd.DataFrame) -> pd.DataFrame:
    """One row per game: date, teams, final score, winner."""
    if df is None or df.empty:
        return pd.DataFrame()
    g = df.groupby("game_pk", sort=False).agg(
        date=("game_date", "first"), game_type=("game_type", "first"),
        home=("home_team", "first"), away=("away_team", "first"),
        home_r=("post_home_score", "max"), away_r=("post_away_score", "max"),
        innings=("inning", "max"),
    ).reset_index()
    g["home_r"] = g["home_r"].astype(int)
    g["away_r"] = g["away_r"].astype(int)
    g["winner"] = np.where(g["home_r"] > g["away_r"], g["home"], np.where(g["away_r"] > g["home_r"], g["away"], ""))
    g["loser"] = np.where(g["winner"] == g["home"], g["away"], np.where(g["winner"] == g["away"], g["home"], ""))
    return g.sort_values(["date", "game_pk"]).reset_index(drop=True)


def line_score(gdf: pd.DataFrame) -> dict:
    gt = games_table(gdf).iloc[0]
    n_inn = max(9, int(gdf["inning"].max()))
    rows = {}
    for side, team, topbot in (("away", gt["away"], "Top"), ("home", gt["home"], "Bot")):
        d = gdf[gdf["inning_topbot"] == topbot]
        by_inn = d.groupby("inning")["runs"].sum()
        played = set(d["inning"].unique())
        cells = []
        for i in range(1, n_inn + 1):
            if i in played:
                cells.append(int(by_inn.get(i, 0)))
            else:
                cells.append("X" if side == "home" and i >= 9 else "")
        pa = d[d["is_pa"]]
        rows[side] = dict(team=team, innings=cells, R=int(gt[f"{side}_r"]),
                          H=int(pa["ev"].isin(HIT_EVENTS).sum()),
                          E=int((gdf[gdf["fld_team"] == team]["ev"] == "field_error").sum()))
    rows["n_inn"] = n_inn
    return rows


def decisions(gdf: pd.DataFrame) -> dict:
    """Approximate W/L/SV for one game. Returns {'W': pid, 'L': pid, 'SV': pid|None}."""
    gt = games_table(gdf).iloc[0]
    if not gt["winner"]:
        return {}
    win, lose = gt["winner"], gt["loser"]
    d = gdf.reset_index(drop=True)
    home_lead_after = d["post_home_score"] - d["post_away_score"]
    win_sign = 1 if win == gt["home"] else -1
    lead_after = home_lead_after * win_sign
    lead_before = (d["home_score"] - d["away_score"]) * win_sign
    # last pitch where winning team went from not-leading to leading
    go_ahead = d.index[(lead_before <= 0) & (lead_after > 0)]
    if len(go_ahead) == 0:
        return {}
    i = int(go_ahead[-1])
    L = int(d.loc[i, "pitcher"])
    # winning team's pitcher of record: last winning-team pitcher to pitch at or before i
    wp_rows = d.loc[:i][d.loc[:i, "fld_team"] == win]
    if wp_rows.empty:
        wp_rows = d[d["fld_team"] == win].head(1)
    W = int(wp_rows["pitcher"].iloc[-1])
    wd = d[d["fld_team"] == win]
    starter = int(wd["pitcher"].iloc[0])
    if W == starter and wd[wd["pitcher"] == starter]["outs_rec"].sum() < 15 and gt["innings"] >= 9:
        relievers = [p for p in dict.fromkeys(wd["pitcher"]) if p != starter]
        if relievers:
            # most outs among relievers
            outs = wd[wd["pitcher"].isin(relievers)].groupby("pitcher")["outs_rec"].sum()
            W = int(outs.idxmax())
    SV = None
    finisher = int(wd["pitcher"].iloc[-1])
    if finisher != W:
        entry = wd[wd["pitcher"] == finisher].index[0]
        lead_at_entry = lead_before.loc[entry]
        f_outs = wd[wd["pitcher"] == finisher]["outs_rec"].sum()
        if 0 < lead_at_entry <= 3 or f_outs >= 9:
            SV = finisher
    return {"W": W, "L": L, "SV": SV}


def all_decisions(df: pd.DataFrame) -> dict:
    out = {"W": {}, "L": {}, "SV": {}}
    for _, g in df.groupby("game_pk", sort=False):
        dec = decisions(g)
        for k in ("W", "L", "SV"):
            p = dec.get(k)
            if p:
                out[k][p] = out[k].get(p, 0) + 1
    return out


def win_prob_points(gdf: pd.DataFrame) -> list[dict]:
    """Home win expectancy after each plate appearance."""
    d = gdf[gdf["is_pa"]]
    if "home_win_exp" not in gdf.columns:
        return []
    base = gdf["home_win_exp"].dropna()
    start = float(base.iloc[0]) if len(base) else 0.5
    wpa_home = np.where(gdf["bat_home"], gdf["wpa_bat"], -gdf["wpa_bat"])
    cum = start + np.cumsum(wpa_home)
    pts = [{"x": 0, "y": start, "inn": "", "des": "Start"}]
    for k, (idx, row) in enumerate(d.iterrows(), start=1):
        y = float(np.clip(cum[gdf.index.get_loc(idx)], 0, 1))
        half = "▲" if row["inning_topbot"] == "Top" else "▼"
        pts.append({"x": k, "y": y, "inn": f"{half}{int(row['inning'])}",
                    "des": str(row.get("des", "") or "")})
    gt = games_table(gdf).iloc[0]
    pts[-1]["y"] = 1.0 if gt["winner"] == gt["home"] else 0.0 if gt["winner"] == gt["away"] else pts[-1]["y"]
    return pts


def top_plays(gdf: pd.DataFrame, names: dict, n: int = 8) -> list[dict]:
    pa = gdf[gdf["is_pa"]].copy()
    if pa.empty:
        return []
    pa["abs"] = pa["wpa_bat"].abs()
    out = []
    for _, r in pa.sort_values("abs", ascending=False).head(n).iterrows():
        out.append(dict(
            inning=f"{'Top' if r['inning_topbot'] == 'Top' else 'Bot'} {int(r['inning'])}",
            batter=names.get(int(r["batter"]), str(r["batter"])), batter_id=int(r["batter"]),
            team=r["bat_team"], des=str(r.get("des", "") or r["ev"]), wpa=float(r["wpa_bat"]),
            score=f"{int(r['post_away_score'])}-{int(r['post_home_score'])}",
        ))
    return out


# ======================================================================= series
def series_list(post: pd.DataFrame, year: int) -> list[dict]:
    """Group postseason games into series."""
    gt = games_table(post)
    if gt.empty:
        return []
    gt["pair"] = gt.apply(lambda r: tuple(sorted([r["home"], r["away"]])), axis=1)
    out = []
    for (gtype, pair), games in gt.groupby(["game_type", "pair"], sort=False):
        games = games.sort_values("date")
        wins = games["winner"].value_counts()
        a, b = pair
        wa, wb = int(wins.get(a, 0)), int(wins.get(b, 0))
        lg = "" if gtype == "W" else league(a, year)
        best_of = {"W": 7, "L": 7, "D": 5, "F": 1 if (2012 <= year <= 2021 and year != 2020) else 3}[gtype]
        if year < 2012 and gtype == "F":
            best_of = 1
        need = best_of // 2 + 1
        if wa >= need or wb >= need:
            winner, loser = (a, b) if wa > wb else (b, a)
            status = "final"
        else:
            winner, loser = (a, b) if wa >= wb else (b, a)
            status = "live"
        first = games.iloc[0]
        out.append(dict(
            id=f"{gtype}-{a}-{b}", game_type=gtype, round=round_name(gtype, year, lg),
            league=lg, teams=[a, b], winner=winner, loser=loser,
            w=max(wa, wb), l=min(wa, wb), status=status, best_of=best_of,
            start=games["date"].min(), end=games["date"].max(),
            home_first=first["home"], games=games.to_dict("records"),
            order=ROUND_ORDER.get(gtype, 9),
        ))
    out.sort(key=lambda s: (-s["order"], s["league"], s["start"]))
    return out


def series_df(post: pd.DataFrame, series: dict) -> pd.DataFrame:
    pks = [g["game_pk"] for g in series["games"]]
    return post[post["game_pk"].isin(pks)]


def team_postseason_path(series: list[dict], team: str) -> list[dict]:
    team = canon(team)
    return sorted([s for s in series if team in s["teams"]], key=lambda s: s["order"])


# ======================================================================= team season
def team_record(df: pd.DataFrame, team: str) -> dict:
    gt = games_table(df)
    if gt.empty:
        return {}
    gt = gt[(gt["home"] == team) | (gt["away"] == team)].copy()
    gt["is_home"] = gt["home"] == team
    gt["opp"] = np.where(gt["is_home"], gt["away"], gt["home"])
    gt["rs"] = np.where(gt["is_home"], gt["home_r"], gt["away_r"])
    gt["ra"] = np.where(gt["is_home"], gt["away_r"], gt["home_r"])
    gt["res"] = np.where(gt["rs"] > gt["ra"], "W", np.where(gt["rs"] < gt["ra"], "L", "T"))
    gt["w_cum"] = (gt["res"] == "W").cumsum()
    gt["l_cum"] = (gt["res"] == "L").cumsum()
    # streak
    streaks, cur, n = [], None, 0
    for r in gt["res"]:
        n = n + 1 if r == cur else 1
        cur = r
        streaks.append(f"{r}{n}" if r != "T" else "")
    gt["streak"] = streaks
    W, L = int((gt["res"] == "W").sum()), int((gt["res"] == "L").sum())
    RS, RA = int(gt["rs"].sum()), int(gt["ra"].sum())
    pyth = RS ** 1.83 / (RS ** 1.83 + RA ** 1.83) if RS + RA else 0.5
    G = W + L
    h, a = gt[gt["is_home"]], gt[~gt["is_home"]]
    one_run = gt[(gt["rs"] - gt["ra"]).abs() == 1]
    gt["month"] = gt["date"].dt.strftime("%b")
    months = []
    for m, mg in gt.groupby(gt["date"].dt.month, sort=True):
        months.append(dict(month=mg["month"].iloc[0], W=int((mg["res"] == "W").sum()),
                           L=int((mg["res"] == "L").sum()), RS=int(mg["rs"].sum()), RA=int(mg["ra"].sum())))
    return dict(
        W=W, L=L, G=G, RS=RS, RA=RA, pct=W / G if G else 0,
        pythW=round(pyth * G), pythL=G - round(pyth * G),
        homeW=int((h["res"] == "W").sum()), homeL=int((h["res"] == "L").sum()),
        awayW=int((a["res"] == "W").sum()), awayL=int((a["res"] == "L").sum()),
        oneW=int((one_run["res"] == "W").sum()), oneL=int((one_run["res"] == "L").sum()),
        xW=int(((gt["innings"] > 9) & (gt["res"] == "W")).sum()),
        xL=int(((gt["innings"] > 9) & (gt["res"] == "L")).sum()),
        months=months, log=gt.to_dict("records"),
    )


def merge_fg(table: pd.DataFrame, fg: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    if table.empty:
        return table
    if fg is None or fg.empty:
        for c in cols:
            table[f"fg_{c}"] = np.nan
        table["fg_multi"] = False
        return table
    keep = ["mlbam", "fg_team"] + [c for c in cols if c in fg.columns]
    f = fg[keep].rename(columns={c: f"fg_{c}" for c in cols})
    t = table.merge(f, how="left", left_on="id", right_on="mlbam").drop(columns=["mlbam"])
    t["fg_multi"] = t["fg_team"].astype(str).str.contains(r"- - -|\d+ Tms|---", regex=True)
    for c in cols:
        if f"fg_{c}" not in t:
            t[f"fg_{c}"] = np.nan
    return t
