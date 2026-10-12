"""ReFant — Baseball Reference + Baseball Savant + FanGraphs in one site.

Run:  python app.py   then open http://127.0.0.1:5000
"""
from __future__ import annotations

import datetime as dt
import math
import traceback

from flask import Flask, abort, redirect, render_template, request, url_for

import data
import stats as S
from teams import TEAMS, canon, team_name, league, division

app = Flask(__name__)

FG_BAT = ["WAR", "wRC+", "Off", "Def", "BsR"]
FG_PIT = ["WAR", "ERA", "FIP", "xFIP", "SIERA"]


# ------------------------------------------------------------------ filters
def _nan(v):
    return v is None or (isinstance(v, float) and math.isnan(v))


@app.template_filter("r3")
def r3(v):  # .312 style rate
    if _nan(v):
        return ""
    s = f"{v:.3f}"
    return s[1:] if s.startswith("0.") else s


@app.template_filter("f1")
def f1(v):
    return "" if _nan(v) else f"{v:.1f}"


@app.template_filter("f2")
def f2(v):
    return "" if _nan(v) else f"{v:.2f}"


@app.template_filter("f0")
def f0(v):
    return "" if _nan(v) else f"{v:.0f}"


@app.template_filter("pct")
def pct(v):
    return "" if _nan(v) else f"{v * 100:.1f}%"


@app.template_filter("wpa")
def wpa(v):
    if _nan(v):
        return ""
    return "0.000" if abs(v) < 0.0005 else f"{v:+.3f}"


@app.template_filter("ip")
def ip(outs):
    if _nan(outs):
        return ""
    outs = int(outs)
    return f"{outs // 3}.{outs % 3}"


@app.template_filter("i")
def as_int(v):
    return "" if _nan(v) else f"{int(v)}"


@app.template_filter("recs")
def recs(df):
    if df is None:
        return []
    return df.to_dict("records") if hasattr(df, "to_dict") else list(df)


@app.template_filter("sumna")
def sumna(rows, key):
    if hasattr(rows, "to_dict"):
        rows = rows.to_dict("records")
    vals = [r.get(key) for r in rows if not _nan(r.get(key))]
    return sum(vals) if vals else float("nan")


@app.template_filter("date")
def fdate(v, fmt="%b %-d"):
    try:
        return v.strftime(fmt)
    except Exception:
        return str(v)


# Static-site build sets app.config["STATIC"] = True and app.config["BUILT"] = {
#   "post": {years}, "teams": {"LAD-2024", ...}, "games": {game_pk, ...}, "updated": "..."}
# so pages only link to pages that exist.
def _built():
    return app.config.get("BUILT") if app.config.get("STATIC") else None


def has_team(abbr, year):
    b = _built()
    return True if b is None else f"{canon(abbr)}-{int(year)}" in b["teams"]


def has_game(pk):
    b = _built()
    return True if b is None else int(pk) in b["games"]


def has_post(year):
    b = _built()
    return True if b is None else int(year) in b["post"]


def has_player(pid):
    b = _built()
    try:
        return True if b is None else int(pid) in b["players"]
    except (TypeError, ValueError):
        return False


def has_full(year):
    b = _built()
    return True if b is None else int(year) in b.get("full", set())


@app.context_processor
def helpers():
    b = _built()
    yr, yp = years()
    return dict(team_name=team_name, TEAMS=TEAMS, league=league, now_year=dt.date.today().year,
                YEARS_R=yr, YEARS_P=yp, LATEST=(yr[-1] if yr else (yp[-1] if yp else dt.date.today().year)),
                LATEST_POST=(yp[-1] if yp else None), endpoint=request.endpoint,
                STATIC=bool(app.config.get("STATIC")), BUILT=b, has_team=has_team, has_game=has_game, has_post=has_post,
                has_player=has_player, has_full=has_full,
                built_years=sorted(set(b["post"]) | {int(k.split("-")[1]) for k in b["teams"]}, reverse=True) if b else None,
                savant=lambda pid: f"https://baseballsavant.mlb.com/savant-player/{pid}",
                fg_note=lambda df: "fg_WAR" in df.columns and df["fg_WAR"].notna().any() if df is not None and len(df) else False)


def years():
    """(regular-season years with full league data, postseason years) for this site."""
    if "YEARS_R" in app.config:
        return app.config["YEARS_R"], app.config["YEARS_P"]
    import json
    from pathlib import Path
    cfg = Path(__file__).parent / "site.json"
    c = json.loads(cfg.read_text()) if cfg.exists() else {}
    from siteconf import years as parse
    yr = parse(c.get("full_seasons", ["now"]))
    yp = sorted(set(parse(c.get("postseasons", ["2015-now"]))) | set(yr))
    app.config["YEARS_R"], app.config["YEARS_P"] = yr, yp
    return yr, yp


def _refresh():
    return request.args.get("refresh") == "1"


@app.errorhandler(Exception)
def on_error(e):
    if hasattr(e, "code") and getattr(e, "code", 500) < 500:
        return e
    traceback.print_exc()
    return render_template("error.html", error=e, tb=traceback.format_exc()), 500


_post_memo: dict = {}


def post_data(year, refresh=False):
    """Prepared postseason pitches + series list, computed once per download."""
    raw = data.get_postseason(year, refresh=refresh)
    key = (year, id(raw), len(raw))
    if key not in _post_memo:
        post = S.prep(raw)
        _post_memo.clear() if len(_post_memo) > 6 else None
        _post_memo[key] = (post, S.series_list(post, year) if post is not None and not post.empty else [])
    return _post_memo[key]


# ------------------------------------------------------------------ pages
@app.route("/season/<int:year>")
def season(year):
    post, series = post_data(year, _refresh())
    rounds = {}
    for s in series:
        rounds.setdefault(s["round"].replace("AL", "").replace("NL", "").strip(), []).append(s)
    champ = next((s for s in series if s["game_type"] == "W" and s["status"] == "final"), None)
    by_div = {}
    for abbr in TEAMS:
        key = f"{league(abbr, year)} {division(abbr, year)}"
        by_div.setdefault(key, []).append(abbr)
    return render_template("season.html", year=year, series=series, rounds=rounds, champ=champ,
                           by_div=dict(sorted(by_div.items())))


@app.route("/postseason/<int:year>/<sid>")
def series_page(year, sid):
    post, series = post_data(year, _refresh())
    s = next((x for x in series if x["id"] == sid), None)
    if s is None:
        abort(404)
    sdf = S.series_df(post, s)
    names = data.get_names()
    dec_all = S.all_decisions(sdf)
    teams = []
    for t in (s["winner"], s["loser"]):
        teams.append(dict(
            abbr=t, name=team_name(t, year),
            bat=S.batting_table(sdf, t, names, order="pa"), bat_tot=S.batting_total(sdf, t),
            pit=S.pitching_table(sdf, t, names, order="season", decisions=dec_all),
            pit_tot=S.pitching_total(sdf, t),
        ))
    games = []
    for i, g in enumerate(s["games"], start=1):
        gdf = sdf[sdf["game_pk"] == g["game_pk"]]
        dec = S.decisions(gdf)
        games.append(dict(g, num=i, W=names.get(dec.get("W"), ""), L=names.get(dec.get("L"), ""),
                          SV=names.get(dec.get("SV"), "") if dec.get("SV") else ""))
    # series leaders by WPA (both teams)
    all_bat = [dict(r, team=t["abbr"]) for t in teams for r in t["bat"].to_dict("records")]
    all_pit = [dict(r, team=t["abbr"]) for t in teams for r in t["pit"].to_dict("records")]
    leaders = sorted(all_bat + all_pit, key=lambda r: -r["WPA"])[:6]
    goats = sorted(all_bat + all_pit, key=lambda r: r["WPA"])[:3]
    path = {t["abbr"]: S.team_postseason_path(series, t["abbr"]) for t in teams}
    return render_template("series.html", year=year, s=s, teams=teams, games=games,
                           leaders=leaders, goats=goats, path=path, all_series=series)


@app.route("/game/<int:game_pk>")
def game(game_pk):
    year = request.args.get("year", type=int)
    team = request.args.get("team")
    gdf = S.prep(data.get_game(game_pk, year=year, team=team))
    if gdf is None or gdf.empty:
        abort(404)
    year = year or int(gdf["game_date"].iloc[0].year)
    names = data.get_names()
    g = S.games_table(gdf).iloc[0].to_dict()
    ls = S.line_score(gdf)
    dec = S.decisions(gdf)
    decd = {k: {v: 1} for k, v in dec.items() if v}
    positions = S.player_positions(gdf)
    sides = []
    for side in ("away", "home"):
        t = g[side]
        bat = S.batting_table(gdf, t, names, order="lineup")
        bat["Pos"] = bat["id"].map(lambda p: "-".join(positions.get(p, [])) or "PH/PR")
        bat["sub"] = bat.index >= 9  # first nine batters to appear = starting lineup
        sides.append(dict(abbr=t, name=team_name(t, year), bat=bat, bat_tot=S.batting_total(gdf, t),
                          pit=S.pitching_table(gdf, t, names, order="appearance", decisions=decd),
                          pit_tot=S.pitching_total(gdf, t)))
    # series context, if postseason
    series = None
    if g["game_type"] in ("F", "D", "L", "W"):
        try:
            for s in post_data(year)[1]:
                if any(x["game_pk"] == game_pk for x in s["games"]):
                    series = s
                    break
        except Exception:
            pass
    wp = S.win_prob_points(gdf)
    return render_template("game.html", g=g, ls=ls, sides=sides, year=year, series=series,
                           dec={k: names.get(v, v) for k, v in dec.items() if v},
                           wp=wp, plays=S.top_plays(gdf, names), team_ctx=team)


import views  # noqa: E402,F401  (registers the FanGraphs-style pages)

if __name__ == "__main__":
    app.run(debug=True, port=5000)
