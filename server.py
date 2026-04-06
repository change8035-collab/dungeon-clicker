import os, json, secrets
from flask import Flask, request, jsonify, redirect, session, send_from_directory, url_for
from google_auth_oauthlib.flow import Flow
from google.oauth2 import id_token
from google.auth.transport import requests as g_requests
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

if os.environ.get('RENDER') is None:
    os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'  # dev only

def get_flow():
    return Flow.from_client_config(
        {"web": {
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        }},
        scopes=['openid', 'https://www.googleapis.com/auth/userinfo.email',
                'https://www.googleapis.com/auth/userinfo.profile']
    )

# ── Auth ──
def get_base_url():
    """Get base URL, forcing HTTPS on production"""
    url = request.host_url.rstrip('/')
    if os.environ.get('RENDER'):
        url = url.replace('http://', 'https://')
    return url

@app.route('/login')
def login():
    flow = get_flow()
    flow.redirect_uri = get_base_url() + '/callback'
    auth_url, state = flow.authorization_url(prompt='select_account')
    session['state'] = state
    return redirect(auth_url)

@app.route('/callback')
def callback():
    try:
        flow = get_flow()
        flow.redirect_uri = get_base_url() + '/callback'
        # Force HTTPS in authorization_response URL
        auth_response = request.url
        if os.environ.get('RENDER'):
            auth_response = auth_response.replace('http://', 'https://')
        flow.fetch_token(authorization_response=auth_response)
        credentials = flow.credentials
        id_info = id_token.verify_oauth2_token(credentials.id_token, g_requests.Request(), GOOGLE_CLIENT_ID)
        session['user'] = {
            'uid': id_info['sub'],
            'email': id_info.get('email', ''),
            'name': id_info.get('name', ''),
            'photo': id_info.get('picture', ''),
            'is_admin': id_info.get('email', '') in ADMIN_EMAILS
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
