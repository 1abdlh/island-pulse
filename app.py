"""Island Pulse: 10 creator dashboard, source polling, and persistent CCU history.

Live numbers are read server-side from Fortnite.gg public creator pages. Daily
island metrics are supplemented opportunistically from Epic's public Data API.
Unknown, stale or blocked readings are never silently replaced with fabricated data.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup, NavigableString
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent
DATA = Path(os.getenv('DATA_DIR', ROOT / 'data'))
DATA.mkdir(parents=True, exist_ok=True)
DB = DATA / 'pulse.db'
SLUGS = ['lolbrotherz', 'obsessedpop', 'mowm', 'timetickz', 'zarugame',
         'mivs', 'broski4life', 'okaykk', 'handshake', 'pozatar']
INTERVAL = max(300, int(os.getenv('POLL_SECONDS', '900')))
EPIC_ENABLED = os.getenv('EPIC_ENRICH', 'true').lower() == 'true'
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (compatible; IslandPulse/1.0; analytics dashboard)',
    'Accept': 'text/html,application/xhtml+xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
}
app = FastAPI(title='Island Pulse')
app.mount('/static', StaticFiles(directory=ROOT / 'static'), name='static')
logging.basicConfig(level=logging.INFO)
LOG = logging.getLogger('islandpulse')
state_lock = threading.RLock()
state: dict[str, Any] = {'refreshing': False, 'last_run': None, 'last_errors': {}}
# Bundled reference data belongs to the source image, not the writable volume.
# When /app/data is mounted as a persistent disk, its contents can be initially empty.
REFERENCE = json.loads((ROOT / 'data' / 'reference.json').read_text(encoding='utf8'))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def numeric(raw: Any) -> float | None:
    if raw is None:
        return None
    t = str(raw).strip().replace(',', '').replace(' ', '').upper()
    m = re.fullmatch(r'(\d+(?:\.\d+)?)([KMBT]?)%?', t)
    if not m:
        return None
    return round(float(m.group(1)) * {'': 1, 'K': 1e3, 'M': 1e6, 'B': 1e9, 'T': 1e12}[m.group(2)], 3)


NUM = r'([\d,]+(?:\.\d+)?(?:\s*[KMBT](?![A-Za-z]))?)'
ISLAND_FIELDS = {
    'ccu': 'Players Now', 'all_time_peak': 'All-Time Peak',
    'minutes_played': 'Minutes Played', 'plays_24h': '24h Plays',
    'favorites_24h': '24h Favorites', 'recommends_24h': '(?:24h Recommends|24h Recs)',
    'players_24h': '24h Players', 'avg_playtime_24h': '24h Avg Playtime',
    'retention_24h': '24h Retention',
}


def value_after(text: str, label: str) -> float | None:
    match = re.search(r'(?:' + label + r')\s*' + NUM, text, re.I)
    return numeric(match.group(1)) if match else None


def parse_creator(html: str, slug: str) -> dict[str, Any]:
    """Parse creator-level counters and map rows; fail closed on missing CCU."""
    soup = BeautifulSoup(html, 'html.parser')
    for x in soup(['script', 'style', 'noscript', 'svg']):
        x.decompose()
    heading = next((h for h in soup.select('h1') if slug.lower() in h.get_text(' ', strip=True).lower()), None)
    if not heading:
        raise ValueError('Creator heading was not found (markup changed or access blocked)')
    tail = ' '.join(str(n).strip() for n in heading.next_elements
                    if isinstance(n, NavigableString) and str(n).strip())
    # Creator header metrics precede the sort controls and map list.
    header = re.split(r'\bSort By\b', tail, maxsplit=1, flags=re.I)[0][:5000]
    header = re.sub(r'#\s*[\d,]+', ' ', header)
    def before(label: str) -> float | None:
        matches = list(re.finditer(NUM + r'\s*' + label + r'\b', header, re.I))
        return numeric(matches[0].group(1)) if matches else None
    ccu = before('Players right now')
    if ccu is None:
        raise ValueError('Current player count not detected; refusing to publish a false 0')
    result = {
        'slug': slug, 'name': slug, 'url': f'https://fortnite.gg/creator/{slug}',
        'ccu': int(ccu), 'minutes_played': before('Minutes Played'),
        'followers': before('Followers'), 'favorites': before('Favorites'),
        'maps': [], 'source': 'live', 'updated_at': utc_now(), 'error': None,
    }
    seen = set()
    for link in soup.select('a[href*="/island/"]'):
        href = link.get('href') or ''
        match = re.search(r'(?<!\d)(\d{4}-\d{4}-\d{4})(?!\d)', href)
        if not match:
            continue
        code = match.group(1)
        if code in seen:
            continue
        seen.add(code)
        candidate = link
        # Tables and rows differ across Fortnite.gg desktop/mobile layouts.
        for _ in range(6):
            if not candidate.parent:
                break
            ancestor = candidate.parent
            probe = ancestor.get_text(' ', strip=True)
            if 'Players Now' in probe and ('24h Plays' in probe or 'All-Time Peak' in probe) and len(probe) < 4000:
                candidate = ancestor
                break
            candidate = ancestor
        row = candidate.get_text(' ', strip=True)
        link_title = link.get_text(' ', strip=True)
        title = re.sub(r'^\s*\d+\s+\d+\s+', '', link_title).strip() or code
        if 'Players Now' in title:
            title = re.sub(r'^\s*\d+\s+\d+\s+', '', title.split('Players Now')[0]).strip()
        island = {'code': code, 'title': title or code,
                  'url': f'https://fortnite.gg/island/{code}'}
        for key, label in ISLAND_FIELDS.items():
            island[key] = value_after(row, label)
        if island['ccu'] is None and island['plays_24h'] is None:
            # An island link outside of the creator's sortable map list.
            continue
        result['maps'].append(island)
    result['map_count'] = len(result['maps']) if result['maps'] else None
    # A missing metric is unknown, not zero; no false totals from partial rows.
    for key in ('plays_24h', 'favorites_24h', 'recommends_24h', 'players_24h'):
        values = [island.get(key) for island in result['maps']]
        result[key] = sum(values) if values and all(v is not None for v in values) else None
    av = [(m['avg_playtime_24h'], m['players_24h']) for m in result['maps']
          if m.get('avg_playtime_24h') is not None and m.get('players_24h') is not None]
    total = sum(weight for _, weight in av)
    result['avg_playtime_24h'] = round(sum(v * weight for v, weight in av) / total, 1) if total and len(av) == len(result['maps']) else None
    return result


def fetch_epic_map(map_data: dict[str, Any]) -> None:
    """Optional daily-bucket official Epic metrics, never a 'live CCU' substitute."""
    if not EPIC_ENABLED:
        return
    code = map_data['code']
    try:
        r = requests.get(f'https://api.fortnite.com/ecosystem/v1/islands/{code}/metrics',
                         headers={'Accept': 'application/json'}, timeout=12)
        r.raise_for_status()
        obj = r.json()
        for field in ('minutesPlayed', 'peakCCU', 'plays', 'uniquePlayers',
                      'favorites', 'recommendations', 'averageMinutesPerPlayer'):
            rows = obj.get(field, [])
            if not isinstance(rows, list):
                continue
            rows = sorted((x for x in rows if isinstance(x, dict) and x.get('timestamp')),
                          key=lambda x: x['timestamp'])
            if rows:
                val = rows[-1].get('value')
                map_data['epic_' + field] = val if isinstance(val, (float, int)) else None
                map_data['epic_day'] = rows[-1]['timestamp'][:10]
        retention = obj.get('retention', [])
        if isinstance(retention, list) and retention:
            last = sorted((x for x in retention if isinstance(x, dict) and x.get('timestamp')),
                          key=lambda x: x['timestamp'])
            if last:
                map_data['epic_d1'] = last[-1].get('d1')
                map_data['epic_d7'] = last[-1].get('d7')
    except Exception as exc:
        LOG.info('Epic enrichment unavailable %s: %s', code, exc)


def fetch_creator(slug: str) -> dict[str, Any]:
    response = requests.get(f'https://fortnite.gg/creator/{slug}', headers=HEADERS, timeout=20)
    response.raise_for_status()
    parsed = parse_creator(response.text, slug)
    if parsed['maps']:
        with ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(fetch_epic_map, parsed['maps']))
    return parsed


def connect() -> sqlite3.Connection:
    db = sqlite3.connect(DB, timeout=30)
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('CREATE TABLE IF NOT EXISTS current (slug TEXT PRIMARY KEY, seen_at TEXT NOT NULL, payload TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS snapshots (slug TEXT NOT NULL, seen_at TEXT NOT NULL, ccu INTEGER NOT NULL, minutes REAL, payload TEXT, PRIMARY KEY(slug, seen_at))')
    columns = [r[1] for r in db.execute('PRAGMA table_info(snapshots)')]
    if 'payload' not in columns:
        db.execute('ALTER TABLE snapshots ADD COLUMN payload TEXT')
    db.commit()
    return db


def save_profile(profile: dict[str, Any], poll_at: str) -> None:
    with connect() as db:
        db.execute('INSERT OR REPLACE INTO current (slug,seen_at,payload) VALUES (?,?,?)',
                   (profile['slug'], profile['updated_at'], json.dumps(profile)))
        db.execute('INSERT OR REPLACE INTO snapshots (slug,seen_at,ccu,minutes,payload) VALUES (?,?,?,?,?)',
                   (profile['slug'], poll_at, profile['ccu'], profile['minutes_played'], json.dumps(profile)))
        db.commit()


def do_refresh() -> None:
    with state_lock:
        if state['refreshing']:
            return
        state['refreshing'] = True
    errs = {}
    poll_at = utc_now()
    try:
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {executor.submit(fetch_creator, s): s for s in SLUGS}
            for future in as_completed(futures):
                slug = futures[future]
                try:
                    profile = future.result()
                    save_profile(profile, poll_at)
                    LOG.info('Updated %s: %s CCU (%s maps)', slug, profile['ccu'], len(profile['maps']))
                except Exception as exc:
                    errs[slug] = str(exc)[:220]
                    LOG.warning('Refresh %s failed: %s', slug, exc)
    finally:
        with state_lock:
            state['last_run'] = utc_now()
            state['last_errors'] = errs
            state['refreshing'] = False


def loop() -> None:
    while True:
        do_refresh()
        time.sleep(INTERVAL)


def get_latest() -> list[dict[str, Any]]:
    profiles = {r['slug']: dict(r) for r in REFERENCE['creators']}
    with connect() as db:
        for slug, payload in db.execute('SELECT slug,payload FROM current'):
            if slug in profiles:
                try:
                    profiles[slug] = json.loads(payload)
                except ValueError:
                    pass
    with state_lock:
        errors = state['last_errors'].copy()
    for slug in SLUGS:
        profiles[slug]['error'] = errors.get(slug)
    return [profiles[s] for s in SLUGS]


def dashboard() -> dict[str, Any]:
    creators = get_latest()
    metric_keys = ('ccu', 'minutes_played', 'favorites', 'followers',
                   'plays_24h', 'favorites_24h', 'recommends_24h', 'players_24h')
    totals = {}
    coverage = {}
    for key in metric_keys:
        available = [c.get(key) for c in creators if isinstance(c.get(key), (int, float))]
        totals[key] = round(sum(available), 2) if available else None
        coverage[key] = len(available)
    with connect() as db:
        # Keep only one point for each 10-minute bucket, aggregated per timestamp bucket.
        rows = db.execute('''SELECT seen_at AS bucket, slug, ccu, payload
                             FROM snapshots WHERE seen_at >= ? ORDER BY seen_at''',
                          ((datetime.now(timezone.utc) - timedelta(days=30)).isoformat(timespec='seconds'),)).fetchall()
    bins: dict[str, dict[str, dict]] = {}
    for bucket, slug, ccu, payload in rows:
        try:
            reading = json.loads(payload) if payload else {'ccu': ccu}
        except ValueError:
            reading = {'ccu': ccu}
        bins.setdefault(bucket, {})[slug] = reading
    series_keys = ('ccu', 'minutes_played', 'plays_24h', 'players_24h',
                   'favorites', 'followers', 'favorites_24h', 'recommends_24h')
    history = []
    for bucket, group in sorted(bins.items()):
        pt = {'timestamp': bucket, 'count': len(group)}
        for key in series_keys:
            vals = [p.get(key) for p in group.values()]
            pt[key] = sum(vals) if vals and all(isinstance(x, (int, float)) for x in vals) else None
        history.append(pt)
    history = history[-3000:]
    creator_history = {slug: [{'timestamp': bucket, **{k: reading.get(k) for k in series_keys}}
                               for bucket, group in sorted(bins.items())
                               if (reading := group.get(slug)) is not None]
                       for slug in SLUGS}
    with state_lock:
        stat = dict(state)
    return {'creator_history': creator_history, 'creators': creators, 'totals': totals, 'coverage': coverage,
            'history': history, 'tracked': len(SLUGS), 'state': stat,
            'poll_seconds': INTERVAL, 'generated_at': utc_now(), 'reference_date': REFERENCE['reference_date']}


@app.get('/')
def home():
    return FileResponse(ROOT / 'static' / 'index.html')


@app.get('/api/dashboard')
def api_dashboard():
    return JSONResponse(dashboard(), headers={'Cache-Control': 'no-store'})


@app.get('/api/health')
def health():
    with state_lock:
        result = dict(state)
    return {'ok': True, **result}


@app.post('/api/refresh')
def refresh():
    # Prevent user-triggered scraping storms from a public, unauthenticated page.
    with state_lock:
        if state['refreshing']:
            return JSONResponse({'accepted': False, 'reason': 'A refresh is running'}, status_code=409)
        if state['last_run']:
            elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(state['last_run'])).total_seconds()
            if elapsed < 300:
                return JSONResponse({'accepted': False, 'reason': 'Refresh is rate limited to once every 5 minutes'}, status_code=429)
    threading.Thread(target=do_refresh, daemon=True).start()
    return JSONResponse({'accepted': True}, status_code=202)


# Single worker is intentional: prevent duplicate pollers; use a persistent
# DATA_DIR volume if retaining history across app restarts/deployments matters.
connect().close()
if os.getenv('DISABLE_POLL', '0') != '1':
    threading.Thread(target=loop, daemon=True, name='creator-poller').start()

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=int(os.environ.get('PORT', '8000')))
