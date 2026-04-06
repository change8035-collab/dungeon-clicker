import os, json, secrets, urllib.parse, hashlib, base64
from flask import Flask, request, jsonify, redirect, session, send_from_directory
import requests as http_requests
from supabase import create_client

app = Flask(__name__, static_folder='.', static_url_path='')
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['PREFERRED_URL_SCHEME'] = 'https'

# Trust proxy headers on Render
from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# ── Config ──
GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID', '362966934345-chc1dngqgtifsh0cegqvsvv4v4v7hb50.apps.googleusercontent.com')
GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET', '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')
SUPABASE_URL = os.environ.get('SUPABASE_URL', 'https://pvwcpowcsstdcvcghjff.supabase.co')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY', 'sb_secret_NvQiIbELqA7FGe9W6wq8YA_yAxTiQf0')
ADMIN_EMAILS = ['teuye144@dgsw.hs.kr', 'teuye144@gmail.com']

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# ── Auth ──
def get_redirect_uri():
    if os.environ.get('RENDER'):
        return 'https://dungeon-clicker.onrender.com/callback'
    return 'http://localhost:8090/callback'

@app.route('/login')
def login():
    # PKCE: generate code_verifier and code_challenge
    code_verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b'=').decode()
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode()).digest()
    ).rstrip(b'=').decode()
    session['code_verifier'] = code_verifier

    params = urllib.parse.urlencode({
        'client_id': GOOGLE_CLIENT_ID,
        'redirect_uri': get_redirect_uri(),
        'response_type': 'code',
        'scope': 'openid email profile',
        'prompt': 'select_account',
        'code_challenge': code_challenge,
        'code_challenge_method': 'S256'
    })
    return redirect('https://accounts.google.com/o/oauth2/v2/auth?' + params)

@app.route('/callback')
def callback():
    try:
        code = request.args.get('code')
        if not code:
            return 'No code received', 400

        code_verifier = session.pop('code_verifier', '')

        # Exchange code for tokens (with PKCE)
        token_res = http_requests.post('https://oauth2.googleapis.com/token', data={
            'code': code,
            'client_id': GOOGLE_CLIENT_ID,
            'client_secret': GOOGLE_CLIENT_SECRET,
            'redirect_uri': get_redirect_uri(),
            'grant_type': 'authorization_code',
            'code_verifier': code_verifier
        })
        tokens = token_res.json()

        if 'error' in tokens:
            return f'Token error: {tokens["error"]} - {tokens.get("error_description","")}', 400

        # Get user info
        user_res = http_requests.get('https://www.googleapis.com/oauth2/v2/userinfo',
            headers={'Authorization': 'Bearer ' + tokens['access_token']})
        user_info = user_res.json()

        session['user'] = {
            'uid': user_info['id'],
            'email': user_info.get('email', ''),
            'name': user_info.get('name', ''),
            'photo': user_info.get('picture', ''),
            'is_admin': user_info.get('email', '') in ADMIN_EMAILS
        }
        return redirect('/')
    except Exception as e:
        return f'로그인 오류: {str(e)}', 500

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/')

@app.route('/api/me')
def api_me():
    user = session.get('user')
    if not user:
        return jsonify({'loggedIn': False})
    return jsonify({'loggedIn': True, **user})

# ── Game Save/Load ──
@app.route('/api/save', methods=['POST'])
def api_save():
    user = session.get('user')
    if not user: return jsonify({'error': 'not logged in'}), 401
    data = request.json
    uid = user['uid']
    supabase.table('saves').upsert({
        'uid': uid, 'email': user['email'], 'name': user['name'],
        'photo': user['photo'], 'game_state': data.get('gameState', {})
    }, on_conflict='uid').execute()
    supabase.table('rankings').upsert({
        'uid': uid, 'name': user['name'], 'photo': user['photo'],
        'email': user['email'], 'combat_power': data.get('combatPower', 0),
        'level': data.get('level', 1), 'stage': data.get('stage', 1),
        'class_name': data.get('className', ''), 'class_stage': data.get('classStage', '')
    }, on_conflict='uid').execute()
    return jsonify({'ok': True})

@app.route('/api/load')
def api_load():
    user = session.get('user')
    if not user: return jsonify({'error': 'not logged in'}), 401
    res = supabase.table('saves').select('game_state').eq('uid', user['uid']).execute()
    if res.data and len(res.data) > 0:
        return jsonify({'gameState': res.data[0].get('game_state')})
    return jsonify({'gameState': None})

@app.route('/api/reset', methods=['POST'])
def api_reset():
    user = session.get('user')
    if not user: return jsonify({'error': 'not logged in'}), 401
    uid = user['uid']
    supabase.table('saves').delete().eq('uid', uid).execute()
    supabase.table('rankings').delete().eq('uid', uid).execute()
    supabase.table('user_settings').delete().eq('uid', uid).execute()
    return jsonify({'ok': True})

# ── Rankings ──
@app.route('/api/rankings')
def api_rankings():
    res = supabase.table('rankings').select('*').order('combat_power', desc=True).limit(50).execute()
    return jsonify({'rankings': res.data or []})

# ── Server Settings ──
@app.route('/api/server-settings')
def api_server_settings():
    res = supabase.table('server_settings').select('*').execute()
    settings = {r['key']: r['value'] for r in (res.data or [])}
    return jsonify(settings)

@app.route('/api/server-settings', methods=['POST'])
def api_set_server_settings():
    user = session.get('user')
    if not user or user['email'] not in ADMIN_EMAILS:
        return jsonify({'error': 'forbidden'}), 403
    for k, v in request.json.items():
        supabase.table('server_settings').upsert({'key': k, 'value': float(v)}, on_conflict='key').execute()
    return jsonify({'ok': True})

# ── User Settings ──
@app.route('/api/my-settings')
def api_my_settings():
    user = session.get('user')
    if not user: return jsonify({})
    res = supabase.table('user_settings').select('settings').eq('uid', user['uid']).execute()
    if res.data and len(res.data) > 0:
        return jsonify(res.data[0].get('settings') or {})
    return jsonify({})

@app.route('/api/user-settings/<uid>', methods=['GET','POST'])
def api_user_settings(uid):
    user = session.get('user')
    if not user or user['email'] not in ADMIN_EMAILS:
        return jsonify({'error': 'forbidden'}), 403
    if request.method == 'POST':
        supabase.table('user_settings').upsert({'uid': uid, 'settings': request.json}, on_conflict='uid').execute()
        return jsonify({'ok': True})
    res = supabase.table('user_settings').select('settings').eq('uid', uid).execute()
    if res.data: return jsonify(res.data[0].get('settings') or {})
    return jsonify({})

# ── Admin ──
@app.route('/api/admin/users')
def api_admin_users():
    user = session.get('user')
    if not user or user['email'] not in ADMIN_EMAILS:
        return jsonify({'error': 'forbidden'}), 403
    res = supabase.table('saves').select('uid,email,name,photo').execute()
    return jsonify({'users': res.data or []})

@app.route('/api/admin/give', methods=['POST'])
def api_admin_give():
    user = session.get('user')
    if not user or user['email'] not in ADMIN_EMAILS:
        return jsonify({'error': 'forbidden'}), 403
    data = request.json
    target_uid, field, amount = data['uid'], data['field'], data.get('amount', 0)
    res = supabase.table('saves').select('game_state').eq('uid', target_uid).execute()
    if not res.data: return jsonify({'error': 'not found'}), 404
    gs = res.data[0].get('game_state') or {}
    if isinstance(gs, str): gs = json.loads(gs)
    gs[field] = gs.get(field, 0) + amount
    supabase.table('saves').update({'game_state': gs}).eq('uid', target_uid).execute()
    return jsonify({'ok': True, 'new_value': gs.get(field)})

# ── Static ──
@app.route('/')
def index():
    return send_from_directory('.', 'game.html')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8090, debug=True)
