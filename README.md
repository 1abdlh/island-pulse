# ISLAND PULSE · Fortnite Creator Intelligence

A responsive, real-data Fortnite Creative creator analytics website with all ten requested creator codes pre-configured:

`lolbrotherz`, `obsessedpop`, `mowm`, `timetickz`, `zarugame`, `mivs`, `broski4life`, `okaykk`, `handshake`, `pozatar`.

## Included

- **Combined CCU** and **per-creator CCU** from public Fortnite.gg creator pages.
- Lifetime minutes played, favorites, followers, detected islands.
- Per-island player counts, all-time peak, lifetime minutes, 24h plays, 24h players, 24h favorites, 24h recommendations, average playtime and retention **when available**.
- Optional daily-bucket metrics from **Epic's public Fortnite Data API**: minutes played, peak CCU, plays, unique players, favorites, recommendations, average minutes/player and D1/D7 retention. These are marked *Epic daily* and are not confused with rolling 24h readings.
- Multiple chart metrics and four time ranges; history starts automatically when the backend first records a successful poll.
- Search, sorting, creator details, island explorer, CSV export, graceful missing-data treatment, responsive dark-mode design.
- SQLite history storage, background refresh every **15 minutes** by default.

**Important: data provenance.** The included offline preview is an explicitly labeled *November 11, 2026 reference snapshot*, not live or historical data. It contains approximate numbers from rounded Fortnite.gg page displays. The website shows actual current readings only after the server successfully retrieves them. If Fortnite.gg blocks requests or changes its HTML structure, the site transparently shows cached/reference data and errors rather than displaying fake live zeroes. External network access was not available in the build environment, so scraping of the deployed live source could not be end-to-end verified here.

**Combined CCU:** Summing per-creator simultaneous player counts is a useful portfolio total, but each source page is fetched over several seconds, not at precisely the same instant. **Players 24h:** Sum across maps, not distinct people across creators or maps (a player on two maps counts twice). **All-time peak:** Individual peaks should not be added together because they may occur at different times.

## Preview without installing anything

Open **`Island-Pulse-Preview.html`** (provided separately) in Chrome, Firefox, or Edge. It runs as a single self-contained file with the 10 reference profiles and functioning search, sorting, navigation, creator panels and CSV export. It deliberately does **not** claim to update live without a backend.

## Run live locally

Requires Python 3.10 or newer.

```bash
python -m pip install -r requirements.txt
python app.py
```

Go to `http://localhost:8000`.

The backend polls the 10 URLs from your list when it starts. It then checks approximately every 15 minutes and stores successful readings to `data/pulse.db`. If outbound access to Fortnite.gg is rejected or the page markup differs, look in `/api/health` under `last_errors` and in the terminal/host logs; the server cannot force a third-party site to permit automated access. The dashboard checks its API every minute. You can manually request a refresh using the top-right arrow; requests are limited to once every 5 minutes.

To run unit tests (without external network access):

```bash
DISABLE_POLL=1 python -m unittest discover -s tests -v
```

On Windows CMD, first run `set DISABLE_POLL=1` and then the Python unittest command.

## Put it on the public internet (Render example)

1. Create your own GitHub repository and upload **all files inside the `island-pulse` folder** (including `Dockerfile`).
2. At [render.com](https://render.com), choose **New → Web Service**, connect the repo, and deploy as a **Docker** service. The repository includes a working `Dockerfile`; no custom build command is required.
3. Configure **one instance/worker**. Do not scale replicas or polling will duplicate source requests. Render provides a public `https://...onrender.com` URL once deployed.
4. For **persistent history**, use a hosting plan that supports a persistent disk and mount it at **`/app/data`**. Without a persistent disk, the app will still run, but SQLite history can disappear during a restart/redeploy. Alternatively use a host with persistent volumes (Railway, VPS, etc.).
5. Keep the service always on if you want uninterrupted 24/7 history. Render Free services spin down on idle, interrupting collection and losing unpersisted SQLite data. Paid instance + persistent disk is the simplest option for continuous tracking.
6. Verify live source availability at `https://YOUR-SERVICE.onrender.com/api/health`. `last_run` should become a timestamp after the first refresh, `refreshing` should become `false`, and ideally `last_errors` should be `{}`. Check `https://YOUR-SERVICE.onrender.com/api/dashboard` for `"source": "live"` alongside `"updated_at"` on each creator (a completed attempt alone does not prove data retrieval worked). The dashboard badge should show *Live source · 10/10* when all 10 sources are fresh. A *Reference snapshot* badge means no fresh source data were collected; it is not current CCU. Check Render logs for `Updated <slug>: <CCU>` and `Refresh <slug> failed` entries.

**Configuration variables:**

| Name | Default | Purpose |
|---|---|---|
| `POLL_SECONDS` | `900` | Time between background checks; minimum enforced is 300 s |
| `EPIC_ENRICH` | `true` | Enable optional official Epic daily island metric enrichment |
| `DATA_DIR` | `data/` | Persistent database directory; set to `/app/data` on container hosts |
| `DISABLE_POLL` | `0` | Set to `1` when running tests without requests |
| `PORT` | `8000` | Web port assigned by hosting provider |

**Source behavior:** Fortnite.gg is a third-party website with no documented public creator-profile API for this use case. This project parses its public pages; therefore server-side access, rate limits, HTML changes, and its policies can affect reliability. Respect their terms and avoid aggressive polling. Epic's public Data API is an additional, officially documented **island** metrics source and does not replace Fortnite.gg's instantaneous creator CCU.

## API

- `GET /api/dashboard`: 10 creators, totals, data freshness, map metrics, CCU history, per-creator history and stored multi-metric snapshots.
- `GET /api/health`: collection status and last errors.
- `POST /api/refresh`: trigger a rate-limited refresh (HTTP 202 on acceptance).

The self-contained `Island-Pulse-Preview.html` is intentionally offline: its refresh button cannot fetch the internet. Open the actual deployed website URL instead. If deploying with a persistent disk, this version loads bundled reference metadata from the application image so an empty `/app/data` volume does not prevent startup.

The app is independent, not affiliated with or endorsed by Epic Games or Fortnite.gg. Fortnite and Fortnite Creative trademarks belong to their respective owners.
