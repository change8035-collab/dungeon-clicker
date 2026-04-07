import os, json, secrets, requests as http_requests
from flask import Flask, request, jsonify, send_from_directory
from supabase import create_client

app = Flask(__name__, static_folder='.', static_url_path='')

from werkzeug.middleware.proxy_fix import ProxyFix
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

SUPABASE_URL = os.environ.get('SUPABASE_URL', 'https://pvwcpowcsstdcvcghjff.supabase.co')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY', 'sb_secret_NvQiIbELqA7FGe9W6wq8YA_yAxTiQf0')
ADMIN_EMAILS = os.environ.get('ADMIN_EMAILS', 'teuye144@dgsw.hs.kr').split(',')
GOOGLE_CLIENT_ID = '362966934345-chc1dngqgtifsh0cegqvsvv4v4v7hb50.apps.googleusercontent.com'

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

def get_client_ip():
    return request.headers.get('X-Forwarded-For', request.remote_addr).split(',')[0].strip()

def get_user():
    """Get user by uid header."""
    uid = request.headers.get('X-User-Id', '')
    if uid:
        res = supabase.table('saves').select('uid,name,email').eq('uid', uid).execute()
        if res.data and len(res.data) > 0:
            user = res.data[0]
            user['is_admin'] = user.get('email', '') in ADMIN_EMAILS
            return user
    return None

# ── Auth ──
@app.route('/api/google-login', methods=['POST'])
def api_google_login():
    """Verify Google ID token and create/find user"""
    data = request.json or {}
    token = data.get('credential', '')
    if not token:
        return jsonify({'error': 'no token'}), 400
    # Verify token with Google
    try:
        verify_res = http_requests.get('https://oauth2.googleapis.com/tokeninfo?id_token=' + token)
        if verify_res.status_code != 200:
            return jsonify({'error': 'invalid token'}), 401
        info = verify_res.json()
        if info.get('aud') != GOOGLE_CLIENT_ID:
            return jsonify({'error': 'wrong audience'}), 401
    except:
        return jsonify({'error': 'verification failed'}), 500

    uid = info['sub']  # Google unique user ID
    email = info.get('email', '')
    name = info.get('name', '') or email.split('@')[0]
    photo = info.get('picture', '')
    is_admin = email in ADMIN_EMAILS

    # Check if user exists
    res = supabase.table('saves').select('uid,name,email').eq('uid', uid).execute()
    if res.data and len(res.data) > 0:
        user = res.data[0]
        # Update name/email/photo if changed
        supabase.table('saves').update({'name': name, 'email': email, 'photo': photo}).eq('uid', uid).execute()
        return jsonify({'ok': True, 'uid': uid, 'nickname': user['name'], 'email': email, 'is_admin': is_admin})

    # New user - create
    supabase.table('saves').insert({'uid': uid, 'name': name, 'email': email, 'photo': photo, 'game_state': {}}).execute()
    return jsonify({'ok': True, 'uid': uid, 'nickname': name, 'email': email, 'is_admin': is_admin, 'new': True})

@app.route('/api/auto-login', methods=['POST'])
def api_auto_login():
    """Check if uid exists in DB"""
    data = request.json or {}
    uid = data.get('uid', '')
    if uid:
        res = supabase.table('saves').select('uid,name,email').eq('uid', uid).execute()
        if res.data and len(res.data) > 0:
            user = res.data[0]
            return jsonify({'ok': True, 'uid': user['uid'], 'nickname': user['name'], 'is_admin': user.get('email','') in ADMIN_EMAILS})
    return jsonify({'ok': False, 'needRegister': True})

@app.route('/api/register', methods=['POST'])
def api_register():
    data = request.json
    nickname = (data.get('nickname', '') or '').strip()
    if not nickname or len(nickname) < 2 or len(nickname) > 12:
        return jsonify({'error': '닉네임은 2~12자로 입력해주세요'}), 400
    # Check duplicate
    res = supabase.table('saves').select('uid').eq('name', nickname).execute()
    if res.data and len(res.data) > 0:
        return jsonify({'error': '이미 사용 중인 닉네임입니다'}), 400
    uid = secrets.token_hex(16)
    supabase.table('saves').insert({
        'uid': uid, 'name': nickname, 'email': '', 'photo': '', 'game_state': {}
    }).execute()
    return jsonify({'ok': True, 'uid': uid, 'nickname': nickname, 'is_admin': False})

@app.route('/api/check-nick', methods=['POST'])
def api_check_nick():
    nickname = (request.json.get('nickname', '') or '').strip()
    res = supabase.table('saves').select('uid').eq('name', nickname).execute()
    return jsonify({'available': not (res.data and len(res.data) > 0)})

@app.route('/api/change-nick', methods=['POST'])
def api_change_nick():
    user = get_user()
    if not user: return jsonify({'error': 'not logged in'}), 401
    new_nick = (request.json.get('nickname', '') or '').strip()
    if not new_nick or len(new_nick) < 2 or len(new_nick) > 12:
        return jsonify({'error': '닉네임은 2~12자'}), 400
    res = supabase.table('saves').select('uid').eq('name', new_nick).execute()
    if res.data and len(res.data) > 0:
        return jsonify({'error': '이미 사용 중인 닉네임'}), 400
    supabase.table('saves').update({'name': new_nick}).eq('uid', user['uid']).execute()
    supabase.table('rankings').update({'name': new_nick}).eq('uid', user['uid']).execute()
    return jsonify({'ok': True, 'nickname': new_nick, 'is_admin': new_nick in ADMIN_NICKS})

@app.route('/api/me')
def api_me():
    user = get_user()
    if not user: return jsonify({'loggedIn': False})
    return jsonify({'loggedIn': True, 'uid': user['uid'], 'name': user['name'], 'is_admin': user.get('is_admin', False)})

# ── Game Save/Load (combined sync endpoint) ──
@app.route('/api/sync', methods=['POST'])
def api_sync():
    """Single endpoint: save game + claim rewards + get server settings"""
    user = get_user()
    if not user: return jsonify({'error': 'not logged in'}), 401
    data = request.json or {}
    uid = user['uid']

    # 1. Save game state
    gs = data.get('gameState')
    if gs:
        supabase.table('saves').update({'game_state': gs}).eq('uid', uid).execute()
        supabase.table('rankings').upsert({
            'uid': uid, 'name': user['name'],
            'combat_power': data.get('combatPower', 0),
            'level': data.get('level', 1), 'stage': data.get('stage', 1),
            'knight_stage': data.get('knightStage', 0),
            'archer_stage': data.get('archerStage', 0),
            'rogue_stage': data.get('rogueStage', 0),
            'class_name': data.get('className', ''), 'class_stage': data.get('classStage', '')
        }, on_conflict='uid').execute()

    # 2. Claim pending rewards
    rewards = {}
    messages = []
    us_res = supabase.table('user_settings').select('settings').eq('uid', uid).execute()
    if us_res.data and len(us_res.data) > 0:
        settings = us_res.data[0].get('settings') or {}
        if isinstance(settings, str): settings = json.loads(settings)
        rewards = settings.pop('pending_rewards', {})
        messages = settings.pop('pending_messages', [])
        if rewards or messages:
            supabase.table('user_settings').update({'settings': settings}).eq('uid', uid).execute()

    # 3. Get server settings
    ss_res = supabase.table('server_settings').select('*').execute()
    server_settings = {r['key']: r['value'] for r in (ss_res.data or [])}

    return jsonify({'ok': True, 'rewards': rewards, 'messages': messages, 'serverSettings': server_settings})

# Keep old save endpoint for compatibility
@app.route('/api/save', methods=['POST'])
def api_save():
    user = get_user()
    if not user: return jsonify({'error': 'not logged in'}), 401
    data = request.json
    uid = user['uid']
    supabase.table('saves').update({
        'game_state': data.get('gameState', {})
    }).eq('uid', uid).execute()
    supabase.table('rankings').upsert({
        'uid': uid, 'name': user['name'],
        'combat_power': data.get('combatPower', 0),
        'level': data.get('level', 1), 'stage': data.get('stage', 1),
        'knight_stage': data.get('knightStage', 0),
        'archer_stage': data.get('archerStage', 0),
        'rogue_stage': data.get('rogueStage', 0),
        'class_name': data.get('className', ''), 'class_stage': data.get('classStage', '')
    }, on_conflict='uid').execute()
    return jsonify({'ok': True})

@app.route('/api/load')
def api_load():
    user = get_user()
    if not user: return jsonify({'error': 'not logged in'}), 401
    res = supabase.table('saves').select('game_state').eq('uid', user['uid']).execute()
    if res.data and len(res.data) > 0:
        return jsonify({'gameState': res.data[0].get('game_state')})
    return jsonify({'gameState': None})

@app.route('/api/reset', methods=['POST'])
def api_reset():
    user = get_user()
    if not user: return jsonify({'error': 'not logged in'}), 401
    uid = user['uid']
    supabase.table('saves').delete().eq('uid', uid).execute()
    supabase.table('rankings').delete().eq('uid', uid).execute()
    supabase.table('user_settings').delete().eq('uid', uid).execute()
    return jsonify({'ok': True})

# ── Rankings ──
@app.route('/api/rankings')
def api_rankings():
    tab = request.args.get('tab', 'combat_power')
    order_col = tab if tab in ['combat_power','knight_stage','archer_stage','rogue_stage'] else 'combat_power'
    res = supabase.table('rankings').select('*').order(order_col, desc=True).limit(50).execute()
    return jsonify({'rankings': res.data or []})

# ── Server Settings ──
@app.route('/api/server-settings')
def api_server_settings():
    res = supabase.table('server_settings').select('*').execute()
    return jsonify({r['key']: r['value'] for r in (res.data or [])})

@app.route('/api/server-settings', methods=['POST'])
def api_set_server_settings():
    user = get_user()
    if not user or not user.get('is_admin'): return jsonify({'error': 'forbidden'}), 403
    for k, v in request.json.items():
        supabase.table('server_settings').upsert({'key': k, 'value': float(v)}, on_conflict='key').execute()
    return jsonify({'ok': True})

# ── User Settings ──
@app.route('/api/my-settings')
def api_my_settings():
    user = get_user()
    if not user: return jsonify({})
    res = supabase.table('user_settings').select('settings').eq('uid', user['uid']).execute()
    if res.data and len(res.data) > 0:
        return jsonify(res.data[0].get('settings') or {})
    return jsonify({})

@app.route('/api/user-settings/<uid>', methods=['GET','POST'])
def api_user_settings(uid):
    user = get_user()
    if not user or not user.get('is_admin'): return jsonify({'error': 'forbidden'}), 403
    if request.method == 'POST':
        supabase.table('user_settings').upsert({'uid': uid, 'settings': request.json}, on_conflict='uid').execute()
        return jsonify({'ok': True})
    res = supabase.table('user_settings').select('settings').eq('uid', uid).execute()
    if res.data: return jsonify(res.data[0].get('settings') or {})
    return jsonify({})

# ── Admin ──
@app.route('/api/admin/users')
def api_admin_users():
    user = get_user()
    if not user or not user.get('is_admin'): return jsonify({'error': 'forbidden'}), 403
    res = supabase.table('saves').select('uid,name').execute()
    return jsonify({'users': res.data or []})

@app.route('/api/admin/give', methods=['POST'])
def api_admin_give():
    user = get_user()
    if not user or not user.get('is_admin'): return jsonify({'error': 'forbidden'}), 403
    data = request.json
    target_uid, field, amount = data['uid'], data['field'], data.get('amount', 0)
    res = supabase.table('saves').select('game_state').eq('uid', target_uid).execute()
    if not res.data: return jsonify({'error': 'user not found'}), 404
    gs = res.data[0].get('game_state') or {}
    if isinstance(gs, str): gs = json.loads(gs)
    gs[field] = gs.get(field, 0) + amount
    supabase.table('saves').update({'game_state': gs}).eq('uid', target_uid).execute()
    return jsonify({'ok': True, 'new_value': gs.get(field)})

@app.route('/api/admin/give-all', methods=['POST'])
def api_admin_give_all():
    user = get_user()
    if not user or not user.get('is_admin'): return jsonify({'error': 'forbidden'}), 403
    data = request.json
    field, amount = data['field'], data.get('amount', 0)
    saves_res = supabase.table('saves').select('uid,game_state').execute()
    count = 0
    for row in (saves_res.data or []):
        gs = row.get('game_state') or {}
        if isinstance(gs, str): gs = json.loads(gs)
        gs[field] = gs.get(field, 0) + amount
        supabase.table('saves').update({'game_state': gs}).eq('uid', row['uid']).execute()
        count += 1
    return jsonify({'ok': True, 'count': count})

@app.route('/api/claim-rewards', methods=['POST'])
def api_claim_rewards():
    user = get_user()
    if not user: return jsonify({'error': 'not logged in'}), 401
    uid = user['uid']
    res = supabase.table('user_settings').select('settings').eq('uid', uid).execute()
    if not res.data: return jsonify({'rewards': {}, 'messages': []})
    settings = res.data[0].get('settings') or {}
    if isinstance(settings, str): settings = json.loads(settings)
    pending = settings.pop('pending_rewards', {})
    messages = settings.pop('pending_messages', [])
    if pending or messages:
        supabase.table('user_settings').update({'settings': settings}).eq('uid', uid).execute()
    return jsonify({'rewards': pending, 'messages': messages})

# ── Beacon save (for beforeunload) ──
@app.route('/api/save-beacon', methods=['POST'])
def api_save_beacon():
    uid = request.args.get('uid', '')
    if not uid: return '', 204
    res = supabase.table('saves').select('uid,name').eq('uid', uid).execute()
    if not res.data: return '', 204
    user = res.data[0]
    data = request.json or {}
    supabase.table('saves').update({'game_state': data.get('gameState', {})}).eq('uid', uid).execute()
    supabase.table('rankings').upsert({
        'uid': uid, 'name': user['name'],
        'combat_power': data.get('combatPower', 0),
        'level': data.get('level', 1), 'stage': data.get('stage', 1),
        'knight_stage': data.get('knightStage', 0),
        'archer_stage': data.get('archerStage', 0),
        'rogue_stage': data.get('rogueStage', 0),
        'class_name': data.get('className', ''), 'class_stage': data.get('classStage', '')
    }, on_conflict='uid').execute()
    return '', 204

# ── Static with cache ──
@app.route('/')
def index():
    return send_from_directory('.', 'game.html')

@app.after_request
def add_cache_headers(response):
    if request.path.startswith('/assets/'):
        response.headers['Cache-Control'] = 'public, max-age=604800'  # 7일 캐시
    return response

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8090, debug=True)
