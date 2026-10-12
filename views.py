"""FanGraphs-style pages: home, leaderboards, players, teams, standings."""
from __future__ import annotations

import json
import math
import traceback

import numpy as np
import pandas as pd
from flask import abort, render_template, Response

import league as L
import stats as S
from app import app, years, post_data, has_team, has_player, _nan
from teams import TEAMS, canon, team_name, league as league_of, division

FMT = {k: v[1] for k, v in L.COLS.items()}


# ------------------------------------------------------------------ filters
@app.template_filter("fmt")
def fmt(v, code):
    from jinja2 import Undefined
    if isinstance(v, Undefined):
        return ""
    if v is None or isinstance(v, str) and code != "s":
        return v if isinstance(v, str) else ""
    try:
        if isinstance(v, float) and math.isnan(v):
            return ""
        v = float(v) if code not in ("s",) and not isinstance(v, (int, float)) else v
    except (TypeError, ValueError):
        return ""
    if code == "i":
        return f"{int(round(v))}"
    if code == "r3":
        s = f"{v:.3f}"
        return s[1:] if s.startswith("0.") else ("-" + s[2:] if s.startswith("-0.") else s)
    if code == "p1":
        return f"{v * 100:.1f}%"
    if code == "f0":
        return f"{v:.0f}"
    if code == "f1":
        return f"{v:.1f}"
    if code == "f2":
        return f"{v:.2f}"
    if code == "ip":
        v = int(v)
        return f"{v // 3}.{v % 3}"
    if code == "wpa":
        return "0.00" if abs(v) < 0.005 else f"{v:+.2f}"
    return str(v)


@app.template_filter("pcolor")
def pcolor(p):
    """Savant percentile colour: blue (1) -> grey (50) -> red (100)."""
    if p is None or (isinstance(p, float) and math.isnan(p)):
        return "#c8c8c8"
    p = max(1, min(100, float(p)))
    lo, mid, hi = (51, 97, 168), (200, 200, 200), (210, 45, 73)
    if p < 50:
        t = (p - 1) / 49
        c = [lo[i] + (mid[i] - lo[i]) * t for i in range(3)]
    else:
        t = (p - 50) / 50
        c = [mid[i] + (hi[i] - mid[i]) * t for i in range(3)]
    return "#%02x%02x%02x" % tuple(int(x) for x in c)


@app.context_processor
def col_helpers():
    return dict(COLS=L.COLS, BAT_GROUPS=L.BAT_GROUPS, PIT_GROUPS=L.PIT_GROUPS, GROUP_ORDER=L.GROUP_ORDER)


def _clean(v):
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating, float)):
        return None if math.isnan(v) else round(float(v), 4)
    if isinstance(v, (np.bool_,)):
        return bool(v)
    if isinstance(v, (list, tuple)):
        return [_clean(x) for x in v]
    return v


def _row(df, idx):
    r = df.loc[idx].to_dict()
    r["id"] = int(idx) if not isinstance(idx, tuple) else int(idx[0])
    return r


# ------------------------------------------------------------------ home
@app.route("/")
def index():
    yr, yp = years()
    ctx = dict(s=None)
    if yr:
        y = yr[-1]
        s = L.season(y, "R")
        if s:
            b, p = s["bat"], s["pit"]
            bq, pq = b[b["q"]], p[p["q"]]
            has_war = b["WAR"].notna().any()
            cards = []

            def top(df, key, n=5, asc=False, fmtc=None):
                t = df[df[key].notna()].sort_values(key, ascending=asc).head(n)
                return [dict(id=int(i), Name=r["Name"], Team=r["Team"], v=r[key]) for i, r in t.iterrows()], (fmtc or FMT.get(key, "f2"))
            for title, df, key, asc in (("WAR", b, "WAR", False), ("wRC+", bq, "wRC+", False), ("Home Runs", b, "HR", False), ("xwOBA", bq, "xwOBA", False)):
                if key == "WAR" and not has_war:
                    title, df, key = "wOBA", bq, "wOBA"
                rows, f = top(df, key, asc=asc)
                cards.append(dict(title=title, key=key, rows=rows, fmt=f, side="bat"))
            pcards = []
            for title, df, key, asc in (("WAR", p, "WAR", False), ("K%", pq, "K%", False), ("FIP", pq, "FIP", True), ("Whiff%", pq, "Whiff%", False)):
                if key == "WAR" and not p["WAR"].notna().any():
                    title, df, key = "Strikeouts", p, "SO"
                rows, f = top(df, key, asc=asc)
                pcards.append(dict(title=title, key=key, rows=rows, fmt=f, side="pit"))
            gt = s["games"]
            last = gt["date"].max()
            recent = gt[gt["date"] == last].to_dict("records")
            ctx = dict(s=s, year=y, cards=cards, pcards=pcards, recent=recent, last=last)
    post = None
    if yp:
        try:
            _, series = post_data(yp[-1])
            post = dict(year=yp[-1], champ=next((x for x in series if x["game_type"] == "W" and x["status"] == "final"), None),
                        series=series)
        except Exception:
            traceback.print_exc()
    return render_template("home.html", post=post, **ctx)


# ------------------------------------------------------------------ leaderboards
@app.route("/leaders/<int:year>/<side>")
def leaders(year, side):
    if side not in ("bat", "pit"):
        abort(404)
    s = L.season(year, "R")
    if not s:
        abort(404)
    df = s[side]
    groups = L.BAT_GROUPS if side == "bat" else L.PIT_GROUPS
    keys = sorted({k for g in groups.values() for k in g} | {"PA", "IP", "TBF"})
    keys = [k for k in keys if k in df.columns]
    extra = ["Name", "Team", "teams", "q"] + (["Pos"] if side == "bat" else ["role"])
    rows = []
    for pid, r in df.iterrows():
        o = {"id": int(pid)}
        for k in keys + extra:
            o[k] = _clean(r.get(k))
        rows.append(o)
    have_fg = df["WAR"].notna().any()
    default = "WAR" if have_fg else ("wOBA" if side == "bat" else "IP")
    if side == "pit" and not have_fg:
        default = "IP"
    built = app.config.get("BUILT")
    team_pages = sorted({k.split("-")[0] for k in (built["teams"] if built else [f"{t}-{year}" for t in TEAMS]) if k.endswith(f"-{year}")})
    payload = dict(side=side, year=year, rows=rows, groups=groups, groupOrder=L.GROUP_ORDER, defaultSort=default,
                   cols={k: list(v) for k, v in L.COLS.items()}, teamPages=team_pages,
                   lowerBetter=["K%", "O-Swing%", "SwStr%", "Whiff%"] if side == "bat" else
                   ["BB%", "BB/9", "HR/9", "ERA", "FIP", "xFIP", "SIERA", "RA9", "WHIP", "xwOBA", "xBA", "xSLG", "wOBA", "AVG", "EV", "Barrel%", "HardHit%", "ERA-", "FIP-", "BABIP"])
    teams_here = sorted({t for ts in df["teams"] for t in ts if t in TEAMS})
    positions = ["C", "1B", "2B", "3B", "SS", "LF", "CF", "RF", "DH"]
    yr, _ = years()
    return render_template("leaders.html", year=year, side=side, data=json.dumps(payload, separators=(",", ":")),
                           teams=teams_here, positions=positions, n_q=int(df["q"].sum()), n=len(df),
                           team_games=s["team_games"], have_fg=have_fg, years_r=yr)


# ------------------------------------------------------------------ players
def _player_bundle(pid):
    yr, yp = years()
    ps = L.player_seasons(pid, yr, yp)
    if not any(ps.values()):
        return None
    return ps


@app.route("/player/<int:pid>")
def player(pid):
    ps = _player_bundle(pid)
    if ps is None:
        abort(404)
    yr, yp = years()
    pa = sum(r.get("PA") or 0 for r in ps["bat"] + ps["bat_post"])
    bf = sum(r.get("TBF") or 0 for r in ps["pit"] + ps["pit_post"])
    is_pitcher = bf > pa
    main_rows = (ps["pit"] or ps["pit_post"]) if is_pitcher else (ps["bat"] or ps["bat_post"])
    latest = main_rows[-1]
    name = latest.get("Name") or f"Player {pid}"
    # header facts
    head = dict(name=name, team=(latest.get("teams") or ["—"])[-1], season=latest["season"],
                pos=("P" if is_pitcher else latest.get("Pos", "")), age=latest.get("Age"))
    if is_pitcher:
        head["hand"] = f"Throws {latest.get('throws') or '?'}"
    else:
        head["hand"] = f"Bats {latest.get('stand') or '?'}"

    # Savant percentiles (latest regular season with data)
    pct = None
    side = "pit" if is_pitcher else "bat"
    for row in reversed(ps[side]):
        s = L.season(row["season"], "R")
        tbl = s[f"{side}_pct"] if s else None
        if tbl is not None and pid in tbl.index:
            spec = L.PIT_PCTL if is_pitcher else L.BAT_PCTL
            src = s[side].loc[pid]
            items = []
            for key, label, _ in spec:
                p = tbl.at[pid, key]
                v = src.get(key)
                items.append(dict(label=label, p=None if (p is None or (isinstance(p, float) and math.isnan(p))) else int(p),
                                  v=fmt(v, FMT.get(key, "f1"))))
            pool = (src["TBF"] >= 1.25 * s["team_games"]) if is_pitcher else bool(src["q"])
            pct = dict(year=row["season"], items=items, qualified=bool(pool))
            break

    def section(rows_r, rows_p, side_):
        if not rows_r and not rows_p:
            return None
        return dict(reg=rows_r, reg_tot=L.total_row(rows_r, side_) if len(rows_r) > 1 else None,
                    post=rows_p, post_tot=L.total_row(rows_p, side_) if len(rows_p) > 1 else None)
    bat_sec = section(ps["bat"], ps["bat_post"], "bat")
    pit_sec = section(ps["pit"], ps["pit_post"], "pit")
    # skip a tiny secondary section (e.g. a position player who pitched once is kept, a pitcher's 2 PAs too)
    order = [("pit", pit_sec), ("bat", bat_sec)] if is_pitcher else [("bat", bat_sec), ("pit", pit_sec)]

    # pitch arsenal per season (regular season)
    arsenal = []
    if is_pitcher:
        for row in reversed(ps["pit"]):
            s = L.season(row["season"], "R")
            a = s["arsenal"] if s else None
            if a is not None and pid in a.index.get_level_values(0):
                t = a.loc[pid].sort_values("N", ascending=False).reset_index()
                t["name"] = t["pitch_type"].map(lambda c: L.PITCH_NAMES.get(c, c))
                arsenal.append(dict(year=row["season"], rows=t.to_dict("records")))

    # spray chart (latest regular season, else postseason)
    spray = None
    if not is_pitcher:
        for kind, rows in (("R", ps["bat"]), ("P", ps["bat_post"])):
            if rows:
                s = L.season(rows[-1]["season"], kind)
                sp = s["spray"] if s else None
                if sp is not None:
                    mine = sp[sp["batter"] == pid]
                    if len(mine):
                        pts = []
                        for r in mine.itertuples():
                            cls = {"single": "1b", "double": "2b", "triple": "3b", "home_run": "hr"}.get(r.ev, "out")
                            pts.append((round(r.hc_x, 1), round(r.hc_y, 1), cls))
                        spray = dict(year=rows[-1]["season"], kind=kind, pts=pts)
                        break

    # game logs
    logs = []
    for kind, rows in (("R", ps[side]), ("P", ps[f"{side}_post"])):
        for row in rows:
            s = L.season(row["season"], kind)
            if not s:
                continue
            g = s["pg" if is_pitcher else "bg"]
            if pid not in g.index.get_level_values(0):
                continue
            t = g.loc[pid].copy()
            t = t.join(s["ginfo"][["date", "home", "away", "home_r", "away_r", "game_type"]], how="left")
            out = []
            for pk, r in t.sort_values("date", ascending=False).iterrows():
                team = r["fld_team"] if is_pitcher else r["bat_team"]
                opp = r["bat_team"] if is_pitcher else r["fld_team"]
                home = (not r["bat_home"]) if is_pitcher else bool(r["bat_home"])
                rs, ra = (r["home_r"], r["away_r"]) if home else (r["away_r"], r["home_r"])
                d = r.to_dict()
                d.update(game_pk=int(pk), team=team, opp=opp, at="" if home else "@",
                         result=f"{'W' if rs > ra else 'L'} {int(rs)}-{int(ra)}")
                out.append(d)
            logs.append(dict(label=f"{row['season']}{' Postseason' if kind == 'P' else ''}", year=row["season"], rows=out))
    logs.sort(key=lambda x: (-x["year"], "Postseason" not in x["label"]))

    # dashboard numbers (latest regular season, else postseason)
    lr = latest
    if is_pitcher:
        dash = [("Season", lr["season"], None), ("W-L", f"{lr.get('W', 0)}-{lr.get('L', 0)}", None), ("IP", lr.get("IP"), "ip"),
                ("ERA" if not _nan(lr.get("ERA")) else "RA9", lr.get("ERA") if not _nan(lr.get("ERA")) else lr.get("RA9"), "f2"),
                ("FIP", lr.get("FIP"), "f2"), ("K%", lr.get("K%"), "p1"), ("BB%", lr.get("BB%"), "p1"),
                ("xwOBA", lr.get("xwOBA"), "r3"), ("WAR", lr.get("WAR"), "f1")]
    else:
        dash = [("Season", lr["season"], None), ("PA", lr.get("PA"), "i"), ("HR", lr.get("HR"), "i"),
                ("AVG/OBP/SLG", f"{fmt(lr.get('AVG'), 'r3')}/{fmt(lr.get('OBP'), 'r3')}/{fmt(lr.get('SLG'), 'r3')}", None),
                ("wOBA", lr.get("wOBA"), "r3"), ("xwOBA", lr.get("xwOBA"), "r3"), ("wRC+", lr.get("wRC+"), "f0"),
                ("Barrel%", lr.get("Barrel%"), "p1"), ("WAR", lr.get("WAR"), "f1")]
    dash = [(k, fmt(v, f) if f else v) for k, v, f in dash]
    return render_template("player.html", pid=pid, head=head, pct=pct, order=order, arsenal=arsenal,
                           spray=spray, logs=logs, dash=dash, is_pitcher=is_pitcher)


@app.route("/players.json")
def players_json():
    return Response(json.dumps(_player_index(), separators=(",", ":")), mimetype="application/json")


def _player_index():
    yr, yp = years()
    seen = {}
    for kind, ys in (("P", yp), ("R", yr)):
        for y in ys:
            s = L.season(y, kind)
            if not s:
                continue
            for side in ("bat", "pit"):
                for pid, r in s[side][["Name", "Team"]].iterrows():
                    cur = seen.get(int(pid))
                    if cur is None or y >= cur["y"]:
                        seen[int(pid)] = dict(id=int(pid), n=r["Name"], t=r["Team"], y=y)
    return sorted(seen.values(), key=lambda p: p["n"].split(" ")[-1] + p["n"])


@app.route("/players/")
@app.route("/players")
def players_index():
    allp = _player_index()
    letters = {}
    for p in allp:
        last = p["n"].split(" ")[-1][:1].upper() or "#"
        letters.setdefault(last if last.isalpha() else "#", []).append(p)
    return render_template("players.html", letters=dict(sorted(letters.items())), n=len(allp))


# ------------------------------------------------------------------ teams
@app.route("/team/<team>/<int:year>")
def team_season(team, year):
    team = canon(team)
    if team not in TEAMS:
        abort(404)
    s = L.season(year, "R")
    if not s:
        abort(404)
    d = s["d"]
    rec = S.team_record(d[(d["home_team"] == team) | (d["away_team"] == team)], team)
    if not rec:
        abort(404)

    def roster(tbl, side):
        if tbl is None or len(tbl) == 0:
            return []
        sub = tbl[tbl.index.get_level_values(1) == team].copy()
        sub.index = sub.index.get_level_values(0)
        base = s[side]
        out = []
        for pid, r in sub.iterrows():
            row = r.to_dict()
            row["id"] = int(pid)
            row["Name"] = base.at[pid, "Name"] if pid in base.index else f"#{pid}"
            if side == "bat":
                row["Pos"] = base.at[pid, "Pos"] if pid in base.index else ""
                single = pid in base.index and len(base.at[pid, "teams"]) == 1
                for k in ("WAR", "Off", "Def", "BsR", "R", "SB", "CS", "wRC+"):
                    row[k] = base.at[pid, k] if single else (row.get("wRC+_calc") if k == "wRC+" else float("nan"))
            else:
                row["role"] = base.at[pid, "role"] if pid in base.index else ""
                single = pid in base.index and len(base.at[pid, "teams"]) == 1
                for k in ("WAR", "ERA", "FIP", "xFIP", "SIERA", "ERA-", "FIP-", "LOB%", "ER"):
                    row[k] = base.at[pid, k] if single else (row.get("FIP_calc") if k == "FIP" else float("nan"))
            out.append(row)
        key = "PA" if side == "bat" else "Outs"
        return sorted(out, key=lambda r: -(r.get(key) or 0))
    bat = roster(s["bteam"], "bat")
    pit = roster(s["pteam"], "pit")
    tb = s["team_bat"].loc[team].to_dict() if team in s["team_bat"].index else None
    tp = s["team_pit"].loc[team].to_dict() if team in s["team_pit"].index else None
    if tb:
        tb["WAR"] = sum(r["WAR"] for r in bat if not _nan(r.get("WAR"))) if any(not _nan(r.get("WAR")) for r in bat) else float("nan")
        tb["G"] = rec["G"]
    if tp:
        tp["WAR"] = sum(r["WAR"] for r in pit if not _nan(r.get("WAR"))) if any(not _nan(r.get("WAR")) for r in pit) else float("nan")
        tp["G"] = rec["G"]
        for k in ("W", "L", "SV", "GS"):
            tp[k] = sum(r.get(k) or 0 for r in pit)
    standing = None
    for div in s["standings"]:
        for i, t in enumerate(div["teams"]):
            if t["team"] == team:
                standing = dict(div=div["div"], place=i + 1, gb=t["GB"], teams=div["teams"])
    # league ranks for a few team stats
    ranks = {}
    tbl = s["team_bat"]
    for k, asc in (("wOBA", False), ("xwOBA", False), ("HR", False), ("K%", True), ("Barrel%", False)):
        if k in tbl:
            ranks[f"bat_{k}"] = int(tbl[k].rank(ascending=asc, method="min").get(team, 0))
    tpt = s["team_pit"]
    for k, asc in (("RA9", True), ("K%", False), ("xwOBA", True), ("Whiff%", False)):
        if k in tpt:
            ranks[f"pit_{k}"] = int(tpt[k].rank(ascending=asc, method="min").get(team, 0))
    post_series, post_bat, post_pit = [], [], []
    try:
        post, all_series = post_data(year)
        if post is not None and not post.empty:
            post_series = S.team_postseason_path(all_series, team)
            sp = L.season(year, "P")
            if post_series and sp:
                post_bat = [dict(r, id=int(i)) for i, r in sp["bat"].iterrows() if team in r["teams"]]
                post_pit = [dict(r, id=int(i)) for i, r in sp["pit"].iterrows() if team in r["teams"]]
                post_bat.sort(key=lambda r: -r["PA"])
                post_pit.sort(key=lambda r: -r["Outs"])
    except Exception:
        traceback.print_exc()
    return render_template("team.html", team=team, year=year, name=team_name(team, year), lg=league_of(team, year),
                           div=division(team, year), rec=rec, bat=bat, pit=pit, tb=tb, tp=tp, standing=standing,
                           ranks=ranks, n_teams=len(tbl), post_series=post_series, post_bat=post_bat, post_pit=post_pit)


@app.route("/standings/<int:year>")
def standings(year):
    s = L.season(year, "R")
    if not s:
        abort(404)
    tb, tp = s["team_bat"], s["team_pit"]
    extra = {}
    for t in tb.index:
        extra[t] = dict(wOBA=tb.at[t, "wOBA"], xwOBA=tb.at[t, "xwOBA"], pxwOBA=tp.at[t, "xwOBA"] if t in tp.index else float("nan"),
                        RA9=tp.at[t, "RA9"] if t in tp.index else float("nan"))
    return render_template("standings.html", year=year, st=s["standings"], extra=extra)


@app.template_filter("dv")
def dv(v):
    """Sort value for a cell (blank for missing)."""
    if isinstance(v, (int, float, np.integer, np.floating)) and not (isinstance(v, (float, np.floating)) and math.isnan(v)):
        return v
    return ""
