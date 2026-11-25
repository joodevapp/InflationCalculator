#!/usr/bin/env python3
"""Rebuild data/cpi.json and data/manifest.json for the Worth app (spec 11.2).

Sources (full history each run):
  US  BLS        CPI-U all items, series CUUR0000SA0
  GB  ONS        CPI index D7BT
  EU  Eurostat   HICP all items, newest base-year index, EU & EEA countries
  KR  KOSIS      Consumer price index, all items (OECD copy when KOSIS is unavailable)

Rules:
  * A country keeps its previous data, with a warning, when the new series is
    shorter than before or brings a new month-over-month move beyond +-10 %.
  * A failed source keeps its countries' previous data; the run fails (exit 1)
    only when every source fails.
  * version, generatedAt and the manifest sha256 change only when the data does.

Environment:
  BLS_API_KEY    BLS API v2 key (without it the slower keyless v1 API is used)
  KOSIS_API_KEY  KOSIS OpenAPI key (without it Korea comes from the OECD)
  KOSIS_TBL_ID, KOSIS_OBJ_L1, KOSIS_ITM_ID  override the KOSIS table below
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import requests

ROOT = Path(__file__).resolve().parent.parent
CPI_PATH = ROOT / 'data' / 'cpi.json'
MANIFEST_PATH = ROOT / 'data' / 'manifest.json'

TIMEOUT = 60
HEADERS = {'User-Agent': 'worth-data/1.0 (monthly CPI file for the Worth app)'}
MAX_MONTHLY_MOVE = 0.10

BLS_SERIES = 'CUUR0000SA0'
BLS_FIRST_YEAR = 1913

ONS_URL = 'https://www.ons.gov.uk/economy/inflationandpriceindices/timeseries/d7bt/mm23/data'

EUROSTAT_URL = 'https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/'
# ECOICOP ver.2 dataset first; the older prc_hicp_midx stopped at 2025-12.
EUROSTAT_DATASETS = [
    ('prc_hicp_minr', {'coicop18': 'TOTAL'}),
    ('prc_hicp_midx', {'coicop': 'CP00'}),
]
EUROSTAT_COUNTRIES = [
    'AT', 'BE', 'BG', 'HR', 'CY', 'CZ', 'DK', 'EE', 'FI', 'FR', 'DE', 'GR', 'HU', 'IE', 'IT',
    'LV', 'LT', 'LU', 'MT', 'NL', 'PL', 'PT', 'RO', 'SK', 'SI', 'ES', 'SE', 'IS', 'NO', 'CH',
]
EUROSTAT_GEO = {'GR': 'EL'}

# Countries outside the euro; everything else in EUROSTAT_COUNTRIES is EUR.
CURRENCY = {
    'CZ': 'CZK', 'DK': 'DKK', 'HU': 'HUF', 'PL': 'PLN', 'RO': 'RON', 'SE': 'SEK',
    'IS': 'ISK', 'NO': 'NOK', 'CH': 'CHF',
}
# First month in euros; the app notes that older amounts are shown in euros.
EURO_SINCE = {
    'AT': '1999-01', 'BE': '1999-01', 'DE': '1999-01', 'ES': '1999-01', 'FI': '1999-01',
    'FR': '1999-01', 'IE': '1999-01', 'IT': '1999-01', 'LU': '1999-01', 'NL': '1999-01',
    'PT': '1999-01', 'GR': '2001-01', 'SI': '2007-01', 'CY': '2008-01', 'MT': '2008-01',
    'SK': '2009-01', 'EE': '2011-01', 'LV': '2014-01', 'LT': '2015-01', 'HR': '2023-01',
    'BG': '2026-01',
}

KOSIS_URL = 'https://kosis.kr/openapi/Param/statisticsParameterData.do'
# "소비자물가지수(2020=100)" all-items table. Check the ids in KOSIS
# (OpenAPI > 통계자료 > URL 생성) and override them with the env vars if they differ.
KOSIS_TBL_ID = os.environ.get('KOSIS_TBL_ID', '').strip() or 'DT_1J22003'
KOSIS_OBJ_L1 = os.environ.get('KOSIS_OBJ_L1', '').strip() or 'T10'
KOSIS_ITM_ID = os.environ.get('KOSIS_ITM_ID', '').strip() or 'T'
KOSIS_FIRST_MONTH = '196501'

OECD_KOR_URL = ('https://sdmx.oecd.org/public/rest/data/'
                'OECD.SDD.TPS,DSD_PRICES@DF_PRICES_ALL,1.0/KOR.M.N.CPI.IX._T.N._Z')

ENTRY_KEYS = ['source', 'seriesLabel', 'currency', 'start', 'latest', 'sourceUpdatedAt', 'values']


class SourceError(Exception):
    pass


def warn(message: str) -> None:
    prefix = '::warning::' if os.environ.get('GITHUB_ACTIONS') else 'WARNING: '
    print(f'{prefix}{message}', file=sys.stderr)


def today() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%d')


def ym_index(ym: str) -> int:
    year, month = ym.split('-')
    return int(year) * 12 + int(month) - 1


def ym_from_index(index: int) -> str:
    return f'{index // 12:04d}-{index % 12 + 1:02d}'


def make_entry(source: str, label: str, currency: str, points: dict[str, float],
               updated: str | None, **extra) -> dict:
    """Turn {'YYYY-MM': value} into the app's entry: start, latest, values (null = gap)."""
    if not points:
        raise SourceError(f'{source}: no values')
    months = sorted(points)
    first, last = ym_index(months[0]), ym_index(months[-1])
    values = [points.get(ym_from_index(i)) for i in range(first, last + 1)]
    entry = {
        'source': source,
        'seriesLabel': label,
        'currency': currency,
        'start': months[0],
        'latest': months[-1],
        'sourceUpdatedAt': updated,
        'values': [None if v is None else round(float(v), 4) for v in values],
    }
    entry.update({k: v for k, v in extra.items() if v})
    return entry


def entry_points(entry: dict) -> dict[str, float]:
    first = ym_index(entry['start'])
    return {ym_from_index(first + i): v for i, v in enumerate(entry['values']) if v is not None}


def get_json(url: str, **kwargs):
    response = requests.get(url, headers=HEADERS, timeout=TIMEOUT, **kwargs)
    response.raise_for_status()
    return response.json()


# ---------------------------------------------------------------- BLS (US)

def fetch_bls() -> dict:
    key = os.environ.get('BLS_API_KEY', '').strip()
    url = 'https://api.bls.gov/publicAPI/v2/timeseries/data/' if key else \
        'https://api.bls.gov/publicAPI/v1/timeseries/data/'
    span = 20 if key else 10  # years per request the API allows
    if not key:
        warn('BLS_API_KEY is not set: using the keyless BLS v1 API (10 years per request).')
    points: dict[str, float] = {}
    last_year = datetime.now(timezone.utc).year
    for start in range(BLS_FIRST_YEAR, last_year + 1, span):
        body = {'seriesid': [BLS_SERIES], 'startyear': str(start),
                'endyear': str(min(start + span - 1, last_year))}
        if key:
            body['registrationkey'] = key
        response = requests.post(url, json=body, headers=HEADERS, timeout=TIMEOUT)
        response.raise_for_status()
        data = response.json()
        if data.get('status') != 'REQUEST_SUCCEEDED':
            raise SourceError(f"BLS: {data.get('status')} {data.get('message')}")
        for item in data['Results']['series'][0]['data']:
            period = item['period']
            if not period.startswith('M') or period == 'M13':  # M13 = annual average
                continue
            try:
                value = float(item['value'])
            except ValueError:  # '-' marks a month BLS did not publish
                continue
            points[f"{item['year']}-{period[1:]}"] = value
    return {'US': make_entry('BLS', 'CPI-U · All items (CUUR0000SA0)', 'USD', points, None)}


# ---------------------------------------------------------------- ONS (GB)

MONTHS = {m: i + 1 for i, m in enumerate(
    ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC'])}


def fetch_ons() -> dict:
    data = get_json(ONS_URL)
    points = {}
    for row in data.get('months', []):
        year, month = row['date'].split()
        points[f'{int(year):04d}-{MONTHS[month.upper()[:3]]:02d}'] = float(row['value'])
    released = (data.get('description', {}).get('releaseDate') or '')[:10] or None
    return {'GB': make_entry('ONS', 'CPI index (D7BT)', 'GBP', points, released)}


# ---------------------------------------------------------------- Eurostat (EU & EEA)

def base_year(unit: str) -> int:
    yy = int(unit[1:])
    return (1900 if yy >= 50 else 2000) + yy


def eurostat_dataset() -> tuple[str, dict, str]:
    """The first dataset that answers, with its newest base-year index unit (I25, I15...)."""
    for dataset, filters in EUROSTAT_DATASETS:
        try:
            data = get_json(EUROSTAT_URL + dataset, params={**filters, 'geo': 'DE', 'lastTimePeriod': 1})
            units = [u for u in data['dimension']['unit']['category']['index'] if re.fullmatch(r'I\d\d', u)]
            if units:
                return dataset, filters, max(units, key=base_year)
        except Exception as error:  # noqa: BLE001
            warn(f'Eurostat {dataset}: {error}')
    raise SourceError('Eurostat: no HICP index dataset answered')


def jsonstat_points(data: dict) -> dict[str, float]:
    sizes = dict(zip(data['id'], data['size']))
    if any(size != 1 for dim, size in sizes.items() if dim != 'time'):
        raise SourceError(f'Eurostat: unexpected dimensions {sizes}')
    values = data.get('value', {})
    points = {}
    for period, position in data['dimension']['time']['category']['index'].items():
        value = values.get(str(position)) if isinstance(values, dict) else \
            (values[position] if position < len(values) else None)
        if value is None:
            continue
        points[period.replace('M', '-') if 'M' in period else period] = float(value)
    return points


def fetch_eurostat() -> dict:
    dataset, filters, unit = eurostat_dataset()
    print(f'Eurostat: {dataset}, unit {unit}')
    out = {}
    for code in EUROSTAT_COUNTRIES:
        try:
            data = get_json(EUROSTAT_URL + dataset,
                            params={**filters, 'geo': EUROSTAT_GEO.get(code, code), 'unit': unit, 'freq': 'M'})
            updated = (data.get('updated') or '')[:10] or None
            out[code] = make_entry('EUROSTAT', 'HICP', CURRENCY.get(code, 'EUR'), jsonstat_points(data),
                                   updated, euroSince=EURO_SINCE.get(code))
        except Exception as error:  # noqa: BLE001
            warn(f'Eurostat {code}: {error} (keeping the previous data)')
    if not out:
        raise SourceError('Eurostat: every country failed')
    return out


# ---------------------------------------------------------------- KOSIS (KR)

def fetch_kosis() -> dict:
    key = os.environ.get('KOSIS_API_KEY', '').strip()
    if not key:
        raise SourceError('KOSIS_API_KEY is not set')
    end = datetime.now(timezone.utc).strftime('%Y%m')
    url = (f'{KOSIS_URL}?method=getList&apiKey={quote(key, safe="")}'
           f'&itmId={KOSIS_ITM_ID}+&objL1={KOSIS_OBJ_L1}+&objL2=&objL3=&objL4=&objL5=&objL6=&objL7=&objL8='
           f'&format=json&jsonVD=Y&prdSe=M&startPrdDe={KOSIS_FIRST_MONTH}&endPrdDe={end}'
           f'&orgId=101&tblId={KOSIS_TBL_ID}')
    data = get_json(url)
    if isinstance(data, dict):
        raise SourceError(f"KOSIS: {data.get('errMsg') or data.get('err') or data}")
    rows = [r for r in data if r.get('C1_NM') == '총지수'] or data
    points = {}
    for row in rows:
        period = str(row.get('PRD_DE', ''))
        try:
            points[f'{period[:4]}-{period[4:6]}'] = float(row['DT'])
        except (KeyError, ValueError):
            continue
    # LST_CHN_DE is when the table last changed, not when a month was released:
    # leave the date to the day the new data arrives.
    return {'KR': make_entry('KOSIS', 'Consumer price index', 'KRW', points, None)}


def fetch_oecd_korea() -> dict:
    """Statistics Korea's CPI as republished by the OECD; used when KOSIS is unavailable."""
    response = requests.get(OECD_KOR_URL, params={'startPeriod': '1965-01', 'format': 'csv'},
                            headers=HEADERS, timeout=TIMEOUT)
    response.raise_for_status()
    points = {}
    for row in csv.DictReader(io.StringIO(response.text)):
        if row.get('OBS_VALUE'):
            points[row['TIME_PERIOD']] = float(row['OBS_VALUE'])
    return {'KR': make_entry('KOSIS', 'Consumer price index', 'KRW', points, None, via='OECD')}


def fetch_korea() -> dict:
    try:
        return fetch_kosis()
    except Exception as error:  # noqa: BLE001
        warn(f'{error}; trying the OECD copy of the Korean CPI.')
        return fetch_oecd_korea()


# ---------------------------------------------------------------- build

def big_moves(points: dict[str, float]) -> set[str]:
    moves = set()
    for month, value in points.items():
        before = points.get(ym_from_index(ym_index(month) - 1))
        if before and abs(value / before - 1) > MAX_MONTHLY_MOVE:
            moves.add(month)
    return moves


def rejection(old: dict | None, new: dict) -> str:
    """Why the new series must not replace the old one ('' when it may)."""
    if not old:
        return ''
    old_points, new_points = entry_points(old), entry_points(new)
    if len(new_points) < len(old_points) or new['latest'] < old['latest']:
        return f"fewer data ({len(new_points)} months to {new['latest']}, " \
               f"was {len(old_points)} to {old['latest']})"
    new_moves = sorted(big_moves(new_points) - big_moves(old_points))
    if new_moves:
        return f'new month-over-month moves beyond 10 % in {", ".join(new_moves[:5])}'
    return ''


def same_data(old: dict | None, new: dict) -> bool:
    if not old:
        return False
    keys = [k for k in set(old) | set(new) if k != 'sourceUpdatedAt']
    return all(old.get(k) == new.get(k) for k in keys)


def ordered(entry: dict) -> dict:
    return {**{k: entry[k] for k in ENTRY_KEYS if k in entry},
            **{k: v for k, v in entry.items() if k not in ENTRY_KEYS}}


def main() -> int:
    if CPI_PATH.exists():
        old = json.loads(CPI_PATH.read_text(encoding='utf-8'))
    else:
        old = {'version': 0, 'generatedAt': None, 'countries': {}}
    old_countries = old.get('countries', {})
    countries = dict(old_countries)

    sources = [('BLS', fetch_bls), ('ONS', fetch_ons), ('Eurostat', fetch_eurostat), ('KOSIS', fetch_korea)]
    answered = 0
    for name, fetch in sources:
        try:
            fetched = fetch()
        except Exception as error:  # noqa: BLE001
            warn(f'{name} failed: {error} (keeping the previous data)')
            continue
        answered += 1
        for code, entry in fetched.items():
            previous = old_countries.get(code)
            if same_data(previous, entry):
                continue
            reason = rejection(previous, entry)
            if reason:
                warn(f'{code}: kept the previous data, {reason}')
                continue
            if not entry.get('sourceUpdatedAt'):
                entry['sourceUpdatedAt'] = today()
            countries[code] = ordered(entry)
            print(f"{code}: updated to {entry['latest']}")

    if answered == 0:
        warn('Every source failed.')
        return 1

    output = os.environ.get('GITHUB_OUTPUT')
    if countries == old_countries:
        print(f"No changes (version {old.get('version', 0)}).")
        if output:
            with open(output, 'a', encoding='utf-8') as fh:
                fh.write('changed=false\n')
        return 0

    version = int(old.get('version', 0)) + 1
    generated = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    cpi = {'version': version, 'generatedAt': generated,
           'countries': {code: countries[code] for code in sorted(countries)}}
    raw = json.dumps(cpi, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    CPI_PATH.parent.mkdir(parents=True, exist_ok=True)
    CPI_PATH.write_bytes(raw)
    manifest = {'version': version, 'generatedAt': generated, 'sha256': hashlib.sha256(raw).hexdigest()}
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    print(f'Wrote version {version} ({len(countries)} countries).')
    if output:
        with open(output, 'a', encoding='utf-8') as fh:
            fh.write(f'changed=true\nversion={version}\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
