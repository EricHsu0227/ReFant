"""Build SavantRef as a static website (for GitHub Pages or any static host).

    python build.py                      # uses site.json, writes ./site
    BASE_PATH=/savantref/ python build.py   # when the site lives under /savantref/

What gets built is set in site.json:
  postseasons        years, a range like "2015-now", or both, e.g. ["2015-now"]
  full_seasons       seasons where every team page and every regular-season box score is built
                     ("now" = the current season). The whole league is downloaded one day at a time;
                     finished days are cached, so later runs only fetch the last few days.
  team_seasons       extra single team seasons, e.g. ["LAD-2024", "NYY-2009"]
  regular_season_box_scores   false to skip the ~2,430 box-score pages per full season

Every season that has team pages also gets its postseason page.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).parent
CONFIG = ROOT / os.environ.get("SITE_CONFIG", "site.json")
OUT = Path(os.environ.get("SITE_OUT", ROOT / "site"))


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def current_season():
    t = dt.date.today()
    return t.year if (t.month, t.day) >= (3, 15) else t.year - 1


def years(spec):
    """'2015-now', 2019, 'now', or a list of those -> sorted list of years."""
    if spec is None:
        return []
    if not isinstance(spec, list):
        spec = [spec]
    out = set()
    for item in spec:
        item = str(item).strip().lower().replace("now", str(current_season()))
        if "-" in item:
            a, b = item.split("-", 1)
            out.update(range(int(a), int(b) + 1))
        elif item:
            out.add(int(item))
    return sorted(y for y in out if 2008 <= y <= dt.date.today().year)


_BASE = "/"
_BASE_URL = "http://localhost"
_APP = None
_CLIENT = None


def _tidy(html: str) -> str:
    """Static links: drop query strings and give page links a trailing slash."""
    pat = re.compile(r'(href="' + re.escape(_BASE) + r'[^"#?]*?)(\?[^"#]*)?(#[^"]*)?"')

    def fix(m):
        path, frag = m.group(1), m.group(3) or ""
        last = path.rsplit("/", 1)[-1]
        if last and "." not in last:
            path += "/"
        return f'{path}{frag}"'
    return pat.sub(fix, html)


def _render_one(task):
    global _CLIENT
    path, dest = task
    if _CLIENT is None:
        _CLIENT = _APP.test_client()
    try:
        r = _CLIENT.get(path, base_url=_BASE_URL)
    except Exception as e:  # noqa: BLE001
        return False, f"{path} -> {e}"
    if r.status_code != 200:
        return False, f"{path} -> HTTP {r.status_code}"
    target = OUT / dest
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_tidy(r.get_data(as_text=True)), encoding="utf-8")
    return True, ""


def main():
    cfg = json.loads(CONFIG.read_text()) if CONFIG.exists() else {}
    base = os.environ.get("BASE_PATH", cfg.get("base_path", "/")) or "/"
    if not base.endswith("/"):
        base += "/"
    if not base.startswith("/"):
        base = "/" + base

    import data
    import stats as S
    from teams import TEAMS, canon
    import app as A

    full_years = years(cfg.get("full_seasons", ["now"]))
    team_list = []
    for item in cfg.get("team_seasons", []):
        t, y = str(item).upper().rsplit("-", 1)
        team_list.append((canon(t), int(str(y).lower().replace("NOW", str(current_season())))))
    post_years = sorted(set(years(cfg.get("postseasons", ["2015-now"]))) | set(full_years) | {y for _, y in team_list})
    box_reg = cfg.get("regular_season_box_scores", True)

    built = {"post": set(), "teams": set(), "games": set(), "updated": ""}
    problems = []

    # ---------------- download / load data
    for y in full_years:
        log(f"Regular season {y}: loading every day (cached days are instant)…")
        df = data.get_regular_season(y)
        if df.empty:
            problems.append(f"no regular-season data for {y}")
            continue
        teams_here = set(df["home_team"]) | set(df["away_team"])
        built["teams"].update(f"{t}-{y}" for t in teams_here if t in TEAMS)
        if box_reg:
            built["games"].update(int(p) for p in df["game_pk"].unique())
        log(f"  {df['game_pk'].nunique()} games, {len(df):,} pitches")
    for t, y in team_list:
        if f"{t}-{y}" in built["teams"]:
            continue
        log(f"Team season {t} {y}…")
        try:
            df = data.get_team_season(t, y)
        except Exception as e:  # noqa: BLE001
            problems.append(f"{t} {y}: {e}")
            continue
        if df.empty:
            problems.append(f"no data for {t} {y}")
            continue
        built["teams"].add(f"{t}-{y}")
        if box_reg:
            built["games"].update(int(p) for p in df["game_pk"].unique())
    for y in post_years:
        log(f"Postseason {y}…")
        try:
            post = data.get_postseason(y)
        except Exception as e:  # noqa: BLE001
            problems.append(f"postseason {y}: {e}")
            continue
        built["post"].add(y)
        if not post.empty:
            built["games"].update(int(p) for p in post["game_pk"].unique())
    built["updated"] = dt.datetime.now(dt.timezone.utc).strftime("%b %-d, %Y %H:%M UTC")

    # ---------------- render
    A.app.config.update(STATIC=True, BUILT=built, TESTING=False, PROPAGATE_EXCEPTIONS=False)
    base_url = "http://localhost" + base.rstrip("/")
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)

    tasks = [("/", "index.html")]
    seen_games = set()
    for y in sorted(built["post"]):
        tasks.append((f"/season/{y}", f"season/{y}/index.html"))
        post, series = A.post_data(y)
        for s in series:
            tasks.append((f"/postseason/{y}/{s['id']}", f"postseason/{y}/{s['id']}/index.html"))
            for g in s["games"]:
                pk = int(g["game_pk"])
                if pk in built["games"] and pk not in seen_games:
                    seen_games.add(pk)
                    tasks.append((f"/game/{pk}?year={y}", f"game/{pk}/index.html"))
    for key in sorted(built["teams"]):
        t, y = key.split("-")
        tasks.append((f"/team/{t}/{y}", f"team/{t}/{y}/index.html"))
    if box_reg:
        for y in sorted({int(k.split("-")[1]) for k in built["teams"]}):
            lg = data._mem.get(f"reg_{y}")
            if lg is not None and not lg.empty:
                pks = {int(p) for p in lg["game_pk"].unique()}
            else:
                pks = set()
                for t, ty in team_list:
                    if ty == y:
                        try:
                            pks |= {int(p) for p in data.get_team_season(t, y)["game_pk"].unique()}
                        except Exception:
                            pass
            for pk in sorted(pks - seen_games):
                seen_games.add(pk)
                tasks.append((f"/game/{pk}?year={y}", f"game/{pk}/index.html"))

    global _BASE, _BASE_URL, _APP
    _BASE, _BASE_URL, _APP = base, base_url, A.app
    workers = int(os.environ.get("BUILD_WORKERS", min(4, os.cpu_count() or 1)))
    log(f"Rendering {len(tasks)} pages with {workers} worker(s)…")
    results = []
    if workers > 1:
        import multiprocessing as mp
        ctx = mp.get_context("fork")
        with ctx.Pool(workers) as pool:
            for i, res in enumerate(pool.imap_unordered(_render_one, tasks, chunksize=8), 1):
                results.append(res)
                if i % 250 == 0:
                    log(f"  {i}/{len(tasks)} pages…")
    else:
        for i, t in enumerate(tasks, 1):
            results.append(_render_one(t))
            if i % 250 == 0:
                log(f"  {i}/{len(tasks)} pages…")
    count = sum(1 for ok, _ in results if ok)
    problems += [msg for ok, msg in results if not ok]

    # ---------------- extras
    with A.app.test_request_context("/", base_url=base_url):
        (OUT / "404.html").write_text(_tidy(A.render_template("notfound.html")), encoding="utf-8")
    shutil.copytree(ROOT / "static", OUT / "static")
    sw = (ROOT / "sw_template.js").read_text().replace("__VERSION__", dt.datetime.now().strftime("%Y%m%d%H%M"))
    (OUT / "sw.js").write_text(sw)
    (OUT / ".nojekyll").write_text("")

    log(f"Done: {count} pages in {OUT}")
    if problems:
        log(f"{len(problems)} problem(s):")
        for p in problems[:40]:
            print("   -", p)
    # fail the build only if nothing useful came out
    if count <= 1:
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
