#!/usr/bin/env python3
"""
Flask server — Israel Alarm Risk Map
• Serves static/index.html behind SSO auth
• OAuth login via Google and/or GitHub (authlib)
• Proxies /api/alarms from Pikud HaOref with local disk cache
"""

import os
import json
import urllib.request
from datetime import date
from functools import wraps

from flask import (
    Flask, session, redirect, url_for,
    jsonify, send_from_directory, request, make_response,
)
from authlib.integrations.flask_client import OAuth

# ── App setup ─────────────────────────────────────────────────────────────────
BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
CACHE_FILE = os.path.join(BASE_DIR, 'alarms_cache.json')
PORT       = int(os.environ.get('PORT', 3030))

app = Flask(__name__, static_folder=os.path.join(BASE_DIR, 'static'))
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-change-before-deploying')
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE']   = os.environ.get('RENDER') == 'true'

# ── OAuth ─────────────────────────────────────────────────────────────────────
oauth = OAuth(app)

_GOOGLE_ID  = os.environ.get('GOOGLE_CLIENT_ID')
_GOOGLE_SEC = os.environ.get('GOOGLE_CLIENT_SECRET')
_GITHUB_ID  = os.environ.get('GITHUB_CLIENT_ID')
_GITHUB_SEC = os.environ.get('GITHUB_CLIENT_SECRET')

if _GOOGLE_ID:
    oauth.register(
        name='google',
        client_id=_GOOGLE_ID,
        client_secret=_GOOGLE_SEC,
        server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
        client_kwargs={'scope': 'openid email profile'},
    )

if _GITHUB_ID:
    oauth.register(
        name='github',
        client_id=_GITHUB_ID,
        client_secret=_GITHUB_SEC,
        access_token_url='https://github.com/login/oauth/access_token',
        authorize_url='https://github.com/login/oauth/authorize',
        api_base_url='https://api.github.com/',
        client_kwargs={'scope': 'read:user user:email'},
    )

# Skip auth entirely in local dev when no OAuth keys are set
NO_AUTH   = not (_GOOGLE_ID or _GITHUB_ID)
PROVIDERS = ([('google', 'Google')]   if _GOOGLE_ID else []) + \
            ([('github', 'GitHub')]   if _GITHUB_ID else [])


# ── Auth helpers ──────────────────────────────────────────────────────────────
def logged_in():
    return NO_AUTH or ('user' in session)


def require_login(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if logged_in():
            return f(*args, **kwargs)
        return redirect(url_for('login_page'))
    return wrapper


# ── Static / app routes ───────────────────────────────────────────────────────
@app.route('/')
@require_login
def index():
    return send_from_directory(app.static_folder, 'index.html')


@app.route('/api/me')
def api_me():
    if NO_AUTH:
        return jsonify({'name': 'Developer (local)', 'email': '', 'avatar': '', 'provider': 'local'})
    if 'user' not in session:
        return jsonify({}), 401
    return jsonify(session['user'])


# ── Login page ────────────────────────────────────────────────────────────────
@app.route('/login')
def login_page():
    if logged_in():
        return redirect('/')
    return make_response(_build_login_html())


@app.route('/login/<provider>')
def login_start(provider):
    if provider not in [p for p, _ in PROVIDERS]:
        return 'OAuth provider not configured', 404
    # On Render the app is behind a TLS proxy; force https scheme for redirect_uri
    scheme = 'https' if os.environ.get('RENDER') else request.scheme
    redirect_uri = url_for('login_callback', provider=provider,
                           _external=True, _scheme=scheme)
    client = oauth.create_client(provider)
    return client.authorize_redirect(redirect_uri)


@app.route('/callback/<provider>')
def login_callback(provider):
    client = oauth.create_client(provider)
    token  = client.authorize_access_token()

    if provider == 'google':
        info = token.get('userinfo') or \
               client.get('https://www.googleapis.com/oauth2/v2/userinfo').json()
        user = {
            'name':     info.get('name', ''),
            'email':    info.get('email', ''),
            'avatar':   info.get('picture', ''),
            'provider': 'google',
        }
    elif provider == 'github':
        info = client.get('user').json()
        email = info.get('email') or ''
        if not email:
            # GitHub can hide the primary email; fetch from emails endpoint
            try:
                emails = client.get('user/emails').json()
                primary = next((e['email'] for e in emails if e.get('primary')), '')
                email = primary
            except Exception:
                pass
        user = {
            'name':     info.get('name') or info.get('login', ''),
            'email':    email,
            'avatar':   info.get('avatar_url', ''),
            'provider': 'github',
        }
    else:
        return 'Unknown provider', 400

    session['user'] = user
    return redirect('/')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login_page'))


# ── Alarm proxy ───────────────────────────────────────────────────────────────
def _load_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return []


def _save_cache(alarms):
    try:
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(alarms, f, ensure_ascii=False)
    except Exception as e:
        print(f'[CACHE] save failed: {e}')


def _merge(existing, new_items):
    seen = {(a.get('alertDate', ''), a.get('data', '')) for a in existing}
    added = 0
    for item in new_items:
        key = (item.get('alertDate', ''), item.get('data', ''))
        if key not in seen:
            existing.append(item)
            seen.add(key)
            added += 1
    return added


@app.route('/api/alarms')
@require_login
def api_alarms():
    today      = date.today().strftime('%d.%m.%Y')
    candidates = [
        'https://www.oref.org.il/warningMessages/alert/History/AlertsHistory.json',
        'https://api.oref.org.il/api/v1/AlertHistory/0',
        'https://api.oref.org.il/api/v1/AlertHistory/1',
        f'https://www.oref.org.il/Shared/Ajax/GetAlarmsHistory.aspx?lang=0&fromDate=01.01.2026&toDate={today}&mode=0',
        f'https://www.oref.org.il/Shared/Ajax/GetAlarmsHistory.aspx?lang=1&fromDate=01.01.2026&toDate={today}&mode=0',
    ]
    req_headers = {
        'User-Agent':      'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36',
        'Referer':         'https://www.oref.org.il/',
        'Accept':          'application/json, text/plain, */*',
        'Accept-Language': 'he-IL,he;q=0.9',
        'X-Requested-With': 'XMLHttpRequest',
        'Origin':          'https://www.oref.org.il',
    }

    cached = _load_cache()

    for url in candidates:
        try:
            req = urllib.request.Request(url, headers=req_headers)
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read()
            text = raw.decode('utf-8', errors='replace').strip()
            if not text or text[0] not in ('[', '{'):
                continue
            parsed = json.loads(text)
            if isinstance(parsed, dict) and 'data' in parsed:
                parsed = parsed['data']
            if not isinstance(parsed, list) or not parsed:
                continue
            added = _merge(cached, parsed)
            print(f'[PROXY] {url} -> {len(parsed)} items, +{added} new (cache={len(cached)})')
            _save_cache(cached)
            break
        except Exception as e:
            print(f'[PROXY] FAIL {url} -> {e}')

    if cached:
        body = json.dumps(cached, ensure_ascii=False)
        r = make_response(body)
        r.headers['Content-Type'] = 'application/json; charset=utf-8'
        return r

    return jsonify({'error': 'No alarm data available and no local cache'}), 502


# ── Login page HTML ───────────────────────────────────────────────────────────
_LOGIN_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Israel Alarm Risk Analyzer — Sign In</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{
  min-height:100vh;background:#0d1117;
  font-family:'Segoe UI',system-ui,sans-serif;
  display:flex;align-items:center;justify-content:center;
  background-image:radial-gradient(ellipse at 50% -10%,rgba(0,80,180,.28) 0%,transparent 60%);
}
.card{
  background:#161b22;border:1px solid #30363d;border-radius:14px;
  padding:44px 40px;width:100%;max-width:390px;text-align:center;
  box-shadow:0 12px 40px rgba(0,0,0,.7);
}
.shield{font-size:52px;margin-bottom:14px;line-height:1}
h1{color:#e6edf3;font-size:21px;font-weight:700;margin-bottom:6px;letter-spacing:.02em}
.sub{color:#8b949e;font-size:13px;line-height:1.55;margin-bottom:8px;display:block}
.badge{
  display:inline-block;background:rgba(220,38,38,.18);color:#f87171;
  border:1px solid rgba(220,38,38,.38);border-radius:20px;
  font-size:10px;font-weight:700;letter-spacing:.09em;padding:2px 11px;
  text-transform:uppercase;margin-bottom:28px;
}
.divider{color:#30363d;font-size:11px;margin:22px 0 18px;position:relative}
.divider::before,.divider::after{
  content:'';position:absolute;top:50%;width:42%;height:1px;background:#30363d;
}
.divider::before{left:0}.divider::after{right:0}
.btn{
  display:flex;align-items:center;justify-content:center;gap:11px;
  width:100%;padding:12px 18px;border-radius:9px;font-size:14px;font-weight:600;
  text-decoration:none;cursor:pointer;transition:opacity .15s,transform .12s;
  margin-bottom:11px;border:1px solid transparent;
}
.btn:hover{opacity:.87;transform:translateY(-1px)}
.btn:active{transform:translateY(0)}
.btn-google{background:#fff;color:#1f1f1f;border-color:#e0e0e0}
.btn-github{background:#24292e;color:#fff;border-color:#444d56}
.no-providers{color:#8b949e;font-size:13px;line-height:1.6;padding:8px 0}
.footer{color:#484f58;font-size:11px;margin-top:26px;line-height:1.7}
.footer a{color:#58a6ff;text-decoration:none}
svg{flex-shrink:0}
</style>
</head>
<body>
<div class="card">
  <div class="shield">🛡️</div>
  <h1>Israel Alarm Risk Analyzer</h1>
  <span class="sub">Route risk mapping based on real-time<br>Pikud HaOref alarm records</span>
  <span class="badge">2026 Live Data</span>
  %%BUTTONS%%
  <p class="footer">
    Sign in to access the map.<br>
    Data sourced from <a href="https://www.oref.org.il" target="_blank" rel="noopener">oref.org.il</a>
  </p>
</div>
</body>
</html>"""

_GOOGLE_BTN = """<a class="btn btn-google" href="/login/google">
  <svg width="18" height="18" viewBox="0 0 48 48">
    <path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"/>
    <path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"/>
    <path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"/>
    <path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.18 1.48-4.97 2.31-8.16 2.31-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"/>
  </svg>
  Continue with Google
</a>"""

_GITHUB_BTN = """<a class="btn btn-github" href="/login/github">
  <svg width="18" height="18" viewBox="0 0 24 24" fill="white">
    <path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57
    0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695
    -.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99
    .105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225
    -.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405
    c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225
    0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3
    0 .315.225.69.825.57A12.02 12.02 0 0 0 24 12c0-6.63-5.37-12-12-12z"/>
  </svg>
  Continue with GitHub
</a>"""


def _build_login_html():
    if PROVIDERS:
        buttons = ''
        if any(p == 'google' for p, _ in PROVIDERS):
            buttons += _GOOGLE_BTN
        if any(p == 'github' for p, _ in PROVIDERS):
            buttons += _GITHUB_BTN
    else:
        buttons = '<p class="no-providers">No OAuth providers configured.<br>Set <code>GOOGLE_CLIENT_ID</code> / <code>GITHUB_CLIENT_ID</code> env vars.</p>'
    return _LOGIN_TEMPLATE.replace('%%BUTTONS%%', buttons)


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == '__main__':
    os.chdir(BASE_DIR)
    debug = os.environ.get('FLASK_DEBUG', '0') == '1'
    app.run(host='0.0.0.0', port=PORT, debug=debug)
