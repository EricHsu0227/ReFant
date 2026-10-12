# SavantRef (ReFant)

A Baseball-Reference-style site whose numbers come from **Baseball Savant** (Statcast pitch-by-pitch) and **FanGraphs** (WAR, wRC+, FIP, xFIP, SIERA). It focuses on postseason series and team seasons.

It runs as a website on **GitHub Pages**. A scheduled GitHub job downloads fresh data, rebuilds every page and publishes the site. It works on phones and can be added to the Home Screen like an app. Pages you've opened stay readable offline.

## Put it online (one-time setup)

1. On github.com, create a new repository named `savantref`. Public is free and unlimited; private also works on most plans, within the Actions minutes limit.
2. Upload everything in this folder to it, including the hidden `.github` folder. The easiest way is **Add file → Upload files** and dragging the folder contents in. If the `.github` folder doesn't upload, create the file `.github/workflows/build.yml` by hand and paste in its contents.
3. Go to **Settings → Pages**, and under **Build and deployment → Source** choose **GitHub Actions**.
4. Go to the **Actions** tab, pick **Build and publish SavantRef**, and press **Run workflow**.

The first run downloads a lot (every postseason since 2015 plus the whole current season, one day at a time), so it can take 30–60 minutes. After that, runs take a few minutes because finished days are cached. The site address appears on the workflow run and in **Settings → Pages**, e.g. `https://<your-name>.github.io/savantref/`.

On your phone, open that address in Safari and choose **Share → Add to Home Screen**.

## Choose what's on the site: `site.json`

```json
{
  "postseasons": ["2015-now"],
  "full_seasons": ["now"],
  "team_seasons": [],
  "regular_season_box_scores": true
}
```

- **postseasons**: which postseasons get a bracket, series pages and box scores. Use years, ranges, or `"now"`.
- **full_seasons**: seasons where all 30 team pages and every regular-season box score are built, e.g. `["2024-now"]`. Each one is about 2,600 pages, and its first build downloads the whole season.
- **team_seasons**: single extra teams, e.g. `["LAD-2019", "CHC-2016"]`. Cheaper than a full season.
- **regular_season_box_scores**: `false` builds team pages without every regular-season box score.

Editing `site.json` on GitHub starts a new build automatically. Each season that has team pages also gets its postseason pages.

## When it updates

- Every 6 hours from March to November, and once a day in the offseason.
- Whenever you change `site.json` or the code.
- Any time you press **Run workflow** on the Actions tab.

Change the times in `.github/workflows/build.yml`.

## Pages

ReFant mixes the three sites: FanGraphs-style navigation, leaderboards and stat tabs; Baseball Savant-style Statcast numbers; Baseball-Reference-style box scores and postseason pages.

| Page | What's on it |
|---|---|
| Home | Season leaders (WAR, wRC+, HR, xwOBA; pitcher WAR, K%, FIP, Whiff%), standings, latest scores, postseason |
| Leaders | Every batter and pitcher, with Dashboard / Standard / Advanced / Batted Ball / Plate Discipline / Statcast / Value tabs. Filter by qualified, minimum PA/IP, team, position, or name |
| Players | Statcast percentile bars, spray chart, pitch mix and arsenal (velocity, spin, movement, whiff%, run value), season-by-season tables in every tab, postseason by series, game logs |
| Teams | Record, rank in wOBA / xwOBA / RA9 / K%, team batting and pitching in every tab, schedule and results |
| Standings | Every division with run differential, Pythagorean record, home/road, last 10, streak |
| Postseason | Bracket, series pages, box scores with win-probability charts |

Stats come from Statcast (computed for every player in one pass) plus FanGraphs WAR, wRC+, ERA, FIP, xFIP, SIERA, R and SB when FanGraphs allows the download. Tap any column header to sort.

## What's reconstructed (marked *)

Statcast doesn't carry official scoring, so these are rebuilt from the pitch data and can occasionally differ from the official record:

- **R** for pitchers: charged to whoever was on the mound when the run scored (no inherited-runner rules)
- **RBI**: runs scoring on the plate appearance, excluding errors and GIDP
- **W / L / SV**: derived from lead changes

FanGraphs values come from the FanGraphs leaderboard API. If FanGraphs blocks a download, those columns are hidden for that run and the rest of the site still builds.

## Run it on your computer instead

```bash
pip install -r requirements.txt
python app.py            # live version at http://127.0.0.1:5000, downloads on demand
python build.py          # or build the static site into ./site
```

## Files

- `build.py`: builds the static site from `site.json`
- `app.py`: pages and formatting (also runs as a live local server)
- `data.py`: Savant / FanGraphs downloads and the cache
- `stats.py`: box scores, series, batting/pitching lines, decisions, win probability
- `teams.py`: team names, leagues, abbreviations
- `templates/`, `static/`: page layouts, styling, icons, table sorting
- `sw_template.js`: offline support
- `.github/workflows/build.yml`: the scheduled build and publish
