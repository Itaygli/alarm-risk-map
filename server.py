#!/usr/bin/env python3
"""
Static file server + Pikud HaOref API proxy
Serves index.html on / and proxies alarm history on /api/alarms
Caches fetched alarms to alarms_cache.json and merges with new data
"""
import http.server
import urllib.request
import json
import os
from datetime import date

PORT = 3030
CACHE_FILE = 'alarms_cache.json'


def load_cache():
    """Load previously cached alarms from disk."""
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return []


def save_cache(alarms):
    """Save merged alarm list to disk."""
    try:
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(alarms, f, ensure_ascii=False)
    except Exception as e:
        print(f'[CACHE] Failed to save: {e}')


def merge_alarms(existing, new_items):
    """Merge new alarms into existing list, deduplicating by (alertDate, data)."""
    seen = {(a.get('alertDate', ''), a.get('data', '')) for a in existing}
    added = 0
    for item in new_items:
        key = (item.get('alertDate', ''), item.get('data', ''))
        if key not in seen:
            existing.append(item)
            seen.add(key)
            added += 1
    return added


class Handler(http.server.SimpleHTTPRequestHandler):

    def do_GET(self):
        if self.path.startswith('/api/alarms'):
            self.proxy_alarms()
        else:
            super().do_GET()

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def proxy_alarms(self):
        today = date.today().strftime('%d.%m.%Y')
        candidates = [
            # Real-time event history (populated during/after active alerts)
            'https://www.oref.org.il/warningMessages/alert/History/AlertsHistory.json',
            # New REST API (lang 0=he)
            'https://api.oref.org.il/api/v1/AlertHistory/0',
            'https://api.oref.org.il/api/v1/AlertHistory/1',
            # Legacy date-range endpoint (may return 404)
            f'https://www.oref.org.il/Shared/Ajax/GetAlarmsHistory.aspx?lang=0&fromDate=01.01.2026&toDate={today}&mode=0',
            f'https://www.oref.org.il/Shared/Ajax/GetAlarmsHistory.aspx?lang=1&fromDate=01.01.2026&toDate={today}&mode=0',
        ]
        req_headers = {
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/122.0.0.0 Safari/537.36'
            ),
            'Referer': 'https://www.oref.org.il/',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'he-IL,he;q=0.9,en-US;q=0.8,en;q=0.7',
            'X-Requested-With': 'XMLHttpRequest',
            'Origin': 'https://www.oref.org.il',
        }

        cached = load_cache()
        new_data_found = False

        for url in candidates:
            try:
                req = urllib.request.Request(url, headers=req_headers)
                with urllib.request.urlopen(req, timeout=15) as resp:
                    data = resp.read()
                text = data.decode('utf-8', errors='replace').strip()
                if not text or text[0] not in ('[', '{'):
                    print(f'[PROXY] Non-JSON from {url}: {text[:60]}')
                    continue
                parsed = json.loads(text)
                # Normalise: accept array or {data:[...]} wrapper
                if isinstance(parsed, dict) and 'data' in parsed:
                    parsed = parsed['data']
                if not isinstance(parsed, list) or len(parsed) == 0:
                    print(f'[PROXY] Empty array from {url}')
                    continue
                added = merge_alarms(cached, parsed)
                print(f'[PROXY] OK: {url} -> {len(parsed)} items, +{added} new (cache now {len(cached)})')
                save_cache(cached)
                new_data_found = True
                break
            except Exception as e:
                print(f'[PROXY] FAIL: {url} -> {e}')

        if not new_data_found and cached:
            print(f'[PROXY] Serving {len(cached)} cached alarms (live API unavailable)')

        if cached:
            out = json.dumps(cached, ensure_ascii=False).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self._cors()
            self.end_headers()
            self.wfile.write(out)
        else:
            err = json.dumps({'error': 'No alarm data available and no cache'}).encode()
            self.send_response(502)
            self.send_header('Content-Type', 'application/json')
            self._cors()
            self.end_headers()
            self.wfile.write(err)

    def _cors(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', '*')

    def log_message(self, fmt, *args):
        if '/api/' in args[0]:
            print(f'[API] {args[0]} {args[1]}')


if __name__ == '__main__':
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    with http.server.HTTPServer(('', PORT), Handler) as httpd:
        print(f'Server running on http://localhost:{PORT}')
        print(f'API proxy on http://localhost:{PORT}/api/alarms')
        httpd.serve_forever()
