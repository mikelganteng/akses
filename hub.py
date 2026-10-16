#!/usr/bin/env python3
"""
PopPay Hub — satu UI untuk semua brand transactions.
Proxy /status /refids /retry ke engine masing-masing (hindari CORS).
Tidak insert deposit, tidak sentuh BO.

VERSION: 1.1.1 — Windows toast FAIL (tab boleh ditutup)
"""
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock, Thread
from urllib.parse import urlparse, urljoin

import requests
from flask import Flask, Response, jsonify, request

if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BRANDS_FILE = os.path.join(BASE_DIR, 'brands.json')
PORT = int(os.environ.get('HUB_PORT') or 3999)
VERSION = '1.1.1'
STATUS_TIMEOUT = 4
PROXY_TIMEOUT = 12
OVERVIEW_TTL = 8
FAIL_TTL = 15
ALLOWED = {
    'status', 'refids', 'retry-refid', 'retry-refids',
    'clear-refid', 'clear-refids', 'bo-history',
    'search-bo-transactions', 'probe', 'login',
}

app = Flask(__name__)
http = requests.Session()
_ad = requests.adapters.HTTPAdapter(pool_connections=40, pool_maxsize=40, max_retries=0)
http.mount('http://', _ad)
http.mount('https://', _ad)

_brands = []
_by_id = {}
_ov_lock = Lock()
_ov_cache = {'ts': 0, 'data': None}
_fail_lock = Lock()
_fail_cache = {'ts': 0, 'data': None}
_seen_fails = set()
_fail_primed = False
CREATE_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0x08000000)


def origin_of(ui):
    p = urlparse(ui or '')
    return f'{p.scheme}://{p.netloc}'


def load_brands():
    global _brands, _by_id
    with open(BRANDS_FILE, 'r', encoding='utf-8') as f:
        raw = json.load(f)
    out = []
    for b in raw:
        ui = str(b.get('ui') or '').strip()
        bid = str(b.get('id') or b.get('code') or '').strip().upper()
        if not bid or not ui:
            continue
        item = {
            'id': bid,
            'name': str(b.get('name') or bid).strip(),
            'code': str(b.get('code') or bid).strip(),
            'ui': ui,
            'base': origin_of(ui),
            'host': urlparse(ui).hostname or '',
            'port': urlparse(ui).port or (443 if urlparse(ui).scheme == 'https' else 80),
        }
        out.append(item)
    _brands = out
    _by_id = {x['id']: x for x in out}
    return out


def get_brand(bid):
    return _by_id.get(str(bid or '').strip().upper())


def _norm_status(brand, js, error=None, http_code=0, ms=0):
    cfg = (js or {}).get('config') or {}
    sess = None
    if js:
        sess = bool(js.get('session_valid'))
    return {
        'id': brand['id'],
        'name': brand['name'],
        'code': brand['code'],
        'ui': brand['ui'],
        'base': brand['base'],
        'host': brand['host'],
        'port': brand['port'],
        'ok': error is None and bool(js),
        'http': http_code,
        'ms': ms,
        'error': error or '',
        'brand': (js or {}).get('brand') or brand['name'],
        'running': bool((js or {}).get('running')),
        'session_valid': sess,
        'session_admin': (js or {}).get('session_admin') or (js or {}).get('session_username') or '',
        'queue_size': int((js or {}).get('queue_size') or 0),
        'jobs_ok': int((js or {}).get('jobs_ok') or 0),
        'jobs_fail': int((js or {}).get('jobs_fail') or 0),
        'last_refid': (js or {}).get('last_refid') or '',
        'enable_submit': bool(cfg.get('enable_submit', True)),
        'tx_count': ((js or {}).get('counts') or {}).get('transactions'),
        'gate_reason': (js or {}).get('gate_reason') or '',
    }


def fetch_status(brand):
    url = urljoin(brand['base'] + '/', 'status')
    t0 = time.time()
    try:
        r = http.get(url, timeout=STATUS_TIMEOUT)
        ms = int((time.time() - t0) * 1000)
        if r.status_code >= 400:
            return _norm_status(brand, None, error=f'HTTP {r.status_code}', http_code=r.status_code, ms=ms)
        try:
            js = r.json()
        except Exception:
            return _norm_status(brand, None, error='status bukan JSON', http_code=r.status_code, ms=ms)
        return _norm_status(brand, js, http_code=r.status_code, ms=ms)
    except Exception as e:
        ms = int((time.time() - t0) * 1000)
        return _norm_status(brand, None, error=str(e)[:160], ms=ms)


def overview(force=False):
    now = time.time()
    with _ov_lock:
        if not force and _ov_cache['data'] and (now - _ov_cache['ts']) < OVERVIEW_TTL:
            return _ov_cache['data']
    rows = []
    with ThreadPoolExecutor(max_workers=min(24, max(4, len(_brands)))) as ex:
        futs = {ex.submit(fetch_status, b): b['id'] for b in _brands}
        for fut in as_completed(futs):
            try:
                rows.append(fut.result())
            except Exception as e:
                bid = futs[fut]
                b = get_brand(bid)
                rows.append(_norm_status(b, None, error=str(e)[:160]))
    order = {b['id']: i for i, b in enumerate(_brands)}
    rows.sort(key=lambda x: order.get(x['id'], 999))
    up = sum(1 for x in rows if x['ok'])
    sess_ok = sum(1 for x in rows if x.get('session_valid') is True)
    down = sum(1 for x in rows if not x['ok'])
    payload = {
        'ok': True,
        'version': VERSION,
        'count': len(rows),
        'up': up,
        'down': down,
        'session_ok': sess_ok,
        'brands': rows,
    }
    with _ov_lock:
        _ov_cache['ts'] = time.time()
        _ov_cache['data'] = payload
    return payload


def proxy_brand(brand, path, req):
    if path not in ALLOWED:
        return {'ok': False, 'error': f'endpoint not allowed: {path}'}, 403
    url = urljoin(brand['base'] + '/', path)
    if req.query_string:
        url = url + '?' + req.query_string.decode('utf-8', 'replace')
    method = req.method.upper()
    headers = {'Accept': 'application/json'}
    body = None
    json_body = None
    if method == 'POST':
        ct = (req.content_type or '')
        if 'json' in ct:
            json_body = req.get_json(silent=True)
            headers['Content-Type'] = 'application/json'
        else:
            body = req.get_data()
    try:
        r = http.request(
            method, url, headers=headers, data=body, json=json_body,
            timeout=PROXY_TIMEOUT, allow_redirects=True,
        )
    except Exception as e:
        return {'ok': False, 'error': str(e)[:200], 'brand': brand['id']}, 502
    try:
        data = r.json()
    except Exception:
        data = {'ok': r.status_code < 400, 'raw': (r.text or '')[:400]}
    if isinstance(data, dict):
        data.setdefault('brand_id', brand['id'])
        data.setdefault('brand_ui', brand['ui'])
    code = r.status_code if r.status_code >= 400 else 200
    return data, code


@app.errorhandler(404)
def not_found(e):
    if request.path.startswith('/api/'):
        return jsonify({'ok': False, 'error': 'not found', 'path': request.path}), 404
    return Response('Not Found', status=404, mimetype='text/plain')


@app.route('/api/brands', methods=['GET'])
def api_brands():
    return jsonify({'ok': True, 'count': len(_brands), 'brands': _brands})


@app.route('/api/overview', methods=['GET'])
def api_overview():
    force = str(request.args.get('force') or '') in ('1', 'true', 'yes')
    return jsonify(overview(force=force))


def scan_fails(per='10', q='', force=False):
    now = time.time()
    with _fail_lock:
        if not force and _fail_cache['data'] and (now - _fail_cache['ts']) < FAIL_TTL:
            return _fail_cache['data']

    def one(b):
        url = urljoin(b['base'] + '/', 'refids')
        params = {'status': 'FAIL', 'per_page': str(per), 'page': '1'}
        if q:
            params['q'] = q
        try:
            r = http.get(url, params=params, timeout=STATUS_TIMEOUT)
            if r.status_code >= 400:
                js = {}
            else:
                try:
                    js = r.json()
                except Exception:
                    js = {}
            rows = js.get('refids') or js.get('rows') or []
            out = []
            for row in rows:
                st = str(row.get('status') or '').upper()
                if st and st != 'FAIL':
                    continue
                item = dict(row)
                item['brand_id'] = b['id']
                item['brand_name'] = b['name']
                item['status'] = st or 'FAIL'
                out.append(item)
            return {
                'id': b['id'],
                'ok': True,
                'rows': out,
                'filtered': len(out),
            }
        except Exception as e:
            return {'id': b['id'], 'ok': False, 'error': str(e)[:120], 'rows': [], 'filtered': 0}

    hits = []
    with ThreadPoolExecutor(max_workers=min(24, max(4, len(_brands)))) as ex:
        futs = [ex.submit(one, b) for b in _brands]
        for fut in as_completed(futs):
            hits.append(fut.result())
    rows = []
    by_brand = {}
    for h in hits:
        chunk = h.get('rows') or []
        by_brand[h['id']] = len(chunk)
        rows.extend(chunk)
    payload = {
        'ok': True,
        'ts': int(time.time()),
        'count': len(rows),
        'brands_ok': sum(1 for h in hits if h.get('ok')),
        'by_brand': by_brand,
        'refids': rows,
    }
    with _fail_lock:
        _fail_cache['ts'] = time.time()
        _fail_cache['data'] = payload
    return payload


def _ps_quote(s):
    return str(s or '').replace("'", "''")[:180]


def windows_toast(title, body):
    title = _ps_quote(title or 'PopPay Hub')
    body = _ps_quote(body)
    script = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        "Add-Type -AssemblyName System.Drawing; "
        "$n = New-Object System.Windows.Forms.NotifyIcon; "
        "$n.Icon = [System.Drawing.SystemIcons]::Error; "
        "$n.Visible = $true; "
        f"$n.ShowBalloonTip(8000, '{title}', '{body}', "
        "[System.Windows.Forms.ToolTipIcon]::Error); "
        "Start-Sleep -Seconds 9; $n.Dispose();"
    )
    try:
        subprocess.Popen(
            ['powershell', '-STA', '-NoProfile', '-WindowStyle', 'Hidden', '-Command', script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW,
        )
    except Exception as e:
        print(f'[ALERT] toast failed: {e}')


def fail_watcher():
    global _fail_primed
    time.sleep(4)
    while True:
        try:
            data = scan_fails(per='15', force=True)
            fresh = []
            for row in (data.get('refids') or []):
                k = f"{row.get('brand_id') or ''}|{row.get('refid') or ''}"
                if k.endswith('|') or not row.get('refid'):
                    continue
                if k not in _seen_fails:
                    if _fail_primed:
                        fresh.append(row)
                    _seen_fails.add(k)
            _fail_primed = True
            if len(_seen_fails) > 2500:
                _seen_fails.clear()
                _fail_primed = False
            if fresh:
                x = fresh[0]
                extra = f' (+{len(fresh)-1})' if len(fresh) > 1 else ''
                title = f"FAIL {x.get('brand_name') or x.get('brand_id') or ''}"
                body = (
                    f"{x.get('username') or ''} {x.get('amount') or ''} "
                    f"{x.get('refid') or ''}{extra}"
                ).strip()
                print(f'[ALERT] {title} {body}', flush=True)
                windows_toast(title, body)
        except Exception as e:
            print(f'[ALERT] watcher: {e}', flush=True)
        time.sleep(20)


@app.route('/api/fail', methods=['GET'])
def api_fail():
    force = str(request.args.get('force') or '') in ('1', 'true', 'yes')
    per = request.args.get('per_page') or '10'
    q = request.args.get('q') or ''
    return jsonify(scan_fails(per=per, q=q, force=force))


@app.route('/api/brand/<bid>/<path:ep>', methods=['GET', 'POST'])
def api_brand(bid, ep):
    b = get_brand(bid)
    if not b:
        return jsonify({'ok': False, 'error': 'unknown brand'}), 404
    data, code = proxy_brand(b, ep, request)
    return jsonify(data), code


@app.route('/', methods=['GET'])
@app.route('/transactions', methods=['GET'])
def ui():
    path = os.path.join(BASE_DIR, 'ui.html')
    with open(path, 'r', encoding='utf-8') as f:
        return Response(f.read(), mimetype='text/html; charset=utf-8')


def main():
    n = len(load_brands())
    print('=' * 60)
    print(f'  PopPay Hub  v{VERSION}')
    print(f'  Brands {n}')
    print(f'  UI     http://127.0.0.1:{PORT}/')
    print(f'  API    http://127.0.0.1:{PORT}/api/overview')
    print('  Notif  Windows toast — tab browser boleh ditutup')
    print('=' * 60)
    Thread(target=fail_watcher, daemon=True).start()
    app.run(host='0.0.0.0', port=PORT, threaded=True)


load_brands()

if __name__ == '__main__':
    main()
