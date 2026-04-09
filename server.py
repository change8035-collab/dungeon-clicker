import os
import time
import uuid
import asyncio
import json
from typing import Optional
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, Response, Query, Path
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

# ── Config ──

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://yzcwgzgnfeeutkhxwmhu.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inl6Y3dnemduZmVldXRraHh3bWh1Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzU3MTY0ODEsImV4cCI6MjA5MTI5MjQ4MX0.9hebqqAWjPajKH2_Sim6kog7dw8_xQD2Ru3vbQd31PM")
ADMIN_EMAILS = os.environ.get("ADMIN_EMAILS", "teuye144@dgsw.hs.kr")
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "362966934345-chc1dngqgtifsh0cegqvsvv4v4v7hb50.apps.googleusercontent.com")
PORT = int(os.environ.get("PORT", "10000"))
SELF_URL = os.environ.get("RENDER_EXTERNAL_URL", f"http://localhost:{PORT}")

REST_URL = SUPABASE_URL + "/rest/v1"

# ── Rate limiter ──

rate_limits: dict[str, float] = {}

def check_rate_limit(key: str, interval_sec: int) -> bool:
    now = time.time()
    last = rate_limits.get(key)
    if last is not None and now - last < interval_sec:
        return False
    rate_limits[key] = now
    if len(rate_limits) > 500:
        rate_limits.clear()
    return True

# ── Server settings cache ──

ss_cache: dict = {}
ss_cache_time: float = 0

# ── Async HTTP client ──

client: httpx.AsyncClient = None  # type: ignore

# ── Supabase helpers ──

def _headers(extra: dict | None = None) -> dict:
    h = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }
    if extra:
        h.update(extra)
    return h


async def db_select(table: str, columns: str, filter_: str | None) -> list[dict]:
    try:
        path = f"/{table}?select={columns}" + (f"&{filter_}" if filter_ else "")
        r = await client.get(REST_URL + path, headers=_headers())
        return r.json()
    except Exception as e:
        print(f"[ERROR] Supabase select {table}: {e}")
        return []


async def db_select_ordered(table: str, columns: str, order_col: str, desc: bool, limit: int) -> list[dict]:
    try:
        direction = "desc" if desc else "asc"
        path = f"/{table}?select={columns}&order={order_col}.{direction}&limit={limit}"
        r = await client.get(REST_URL + path, headers=_headers())
        return r.json()
    except Exception as e:
        print(f"[ERROR] Supabase selectOrdered {table}: {e}")
        return []


async def db_insert(table: str, data: dict):
    try:
        await client.post(REST_URL + f"/{table}", headers=_headers(), content=json.dumps(data))
    except Exception as e:
        print(f"[ERROR] Supabase insert {table}: {e}")


async def db_update(table: str, data: dict, filter_: str):
    try:
        await client.patch(REST_URL + f"/{table}?{filter_}", headers=_headers(), content=json.dumps(data))
    except Exception as e:
        print(f"[ERROR] Supabase update {table}: {e}")


async def db_upsert(table: str, data: dict):
    try:
        headers = _headers({"Prefer": "resolution=merge-duplicates,return=representation"})
        await client.post(REST_URL + f"/{table}", headers=headers, content=json.dumps(data))
    except Exception as e:
        print(f"[ERROR] Supabase upsert {table}: {e}")


async def db_delete(table: str, filter_: str):
    try:
        await client.delete(REST_URL + f"/{table}?{filter_}", headers=_headers())
    except Exception as e:
        print(f"[ERROR] Supabase delete {table}: {e}")


# ── User helpers ──

async def get_user(request: Request) -> dict | None:
    uid = request.headers.get("X-User-Id", "")
    if not uid:
        return None
    rows = await db_select("saves", "uid,name,email", f"uid=eq.{uid}")
    if not rows:
        return None
    user = dict(rows[0])
    email = user.get("email", "")
    user["is_admin"] = email in set(ADMIN_EMAILS.split(","))
    return user


def is_admin(user: dict | None) -> bool:
    return user is not None and user.get("is_admin") is True


async def get_server_settings() -> dict:
    global ss_cache, ss_cache_time
    now = time.time()
    if now - ss_cache_time < 10:
        return ss_cache
    rows = await db_select("server_settings", "*", None)
    m = {}
    for r in rows:
        m[r["key"]] = r["value"]
    ss_cache = m
    ss_cache_time = now
    return ss_cache


# ── Keep-alive scheduler ──

async def keep_alive_loop():
    while True:
        await asyncio.sleep(240)
        try:
            await client.get(f"{SELF_URL}/api/me")
        except Exception:
            pass


# ── App lifecycle ──

@asynccontextmanager
async def lifespan(app: FastAPI):
    global client
    client = httpx.AsyncClient(timeout=30)
    task = asyncio.create_task(keep_alive_loop())
    yield
    task.cancel()
    await client.aclose()


app = FastAPI(lifespan=lifespan)

# ── Static files ──

@app.get("/")
async def index():
    return FileResponse("game.html", media_type="text/html", headers={"Cache-Control": "no-cache"})


@app.get("/game.html")
async def game_html():
    return FileResponse("game.html", media_type="text/html", headers={"Cache-Control": "no-cache"})


app.mount("/assets", StaticFiles(directory="assets"), name="assets")

# ── Auth endpoints ──

@app.post("/api/google-login")
async def google_login(request: Request):
    body = await request.json()
    token = body.get("credential", "")
    if not token:
        return JSONResponse({"error": "no token"}, status_code=400)
    try:
        r = await client.get(f"https://oauth2.googleapis.com/tokeninfo?id_token={token}")
        if r.status_code != 200:
            return JSONResponse({"error": "invalid token"}, status_code=401)
        info = r.json()
        if info.get("aud") != GOOGLE_CLIENT_ID:
            return JSONResponse({"error": "wrong audience"}, status_code=401)

        uid = info["sub"]
        email = info.get("email", "")
        name = info.get("name") or email.split("@")[0]
        photo = info.get("picture", "")
        admin = email in set(ADMIN_EMAILS.split(","))

        existing = await db_select("saves", "uid,name,email", f"uid=eq.{uid}")
        if existing:
            await db_update("saves", {"name": name, "email": email, "photo": photo}, f"uid=eq.{uid}")
            return JSONResponse({"ok": True, "uid": uid, "nickname": existing[0]["name"], "email": email, "is_admin": admin})
        await db_insert("saves", {"uid": uid, "name": name, "email": email, "photo": photo, "game_state": {}})
        return JSONResponse({"ok": True, "uid": uid, "nickname": name, "email": email, "is_admin": admin, "new": True})
    except Exception as e:
        print(f"[ERROR] Google login: {e}")
        return JSONResponse({"error": "verification failed"}, status_code=500)


@app.post("/api/auto-login")
async def auto_login(request: Request):
    body = await request.json()
    uid = body.get("uid", "")
    if uid:
        rows = await db_select("saves", "uid,name,email", f"uid=eq.{uid}")
        if rows:
            u = rows[0]
            email = u.get("email", "")
            return JSONResponse({"ok": True, "uid": u["uid"], "nickname": u["name"],
                                 "is_admin": email in set(ADMIN_EMAILS.split(","))})
    return JSONResponse({"ok": False, "needRegister": True})


@app.post("/api/register")
async def register(request: Request):
    body = await request.json()
    nickname = body.get("nickname", "").strip()
    if len(nickname) < 2 or len(nickname) > 12:
        return JSONResponse({"error": "닉네임은 2~12자로 입력해주세요"}, status_code=400)
    dup = await db_select("saves", "uid", f"name=eq.{nickname}")
    if dup:
        return JSONResponse({"error": "이미 사용 중인 닉네임입니다"}, status_code=400)
    uid = uuid.uuid4().hex[:32]
    await db_insert("saves", {"uid": uid, "name": nickname, "email": "", "photo": "", "game_state": {}})
    return JSONResponse({"ok": True, "uid": uid, "nickname": nickname, "is_admin": False})


@app.post("/api/check-nick")
async def check_nick(request: Request):
    body = await request.json()
    nickname = body.get("nickname", "").strip()
    rows = await db_select("saves", "uid", f"name=eq.{nickname}")
    return JSONResponse({"available": len(rows) == 0})


@app.post("/api/change-nick")
async def change_nick(request: Request):
    user = await get_user(request)
    if not user:
        return JSONResponse({"error": "not logged in"}, status_code=401)
    body = await request.json()
    new_nick = body.get("nickname", "").strip()
    if len(new_nick) < 2 or len(new_nick) > 12:
        return JSONResponse({"error": "닉네임은 2~12자"}, status_code=400)
    dup = await db_select("saves", "uid", f"name=eq.{new_nick}")
    if dup:
        return JSONResponse({"error": "이미 사용 중인 닉네임"}, status_code=400)
    uid = user["uid"]
    await db_update("saves", {"name": new_nick}, f"uid=eq.{uid}")
    await db_update("rankings", {"name": new_nick}, f"uid=eq.{uid}")
    return JSONResponse({"ok": True, "nickname": new_nick, "is_admin": user["is_admin"]})


@app.get("/api/me")
async def me(request: Request):
    user = await get_user(request)
    if not user:
        return JSONResponse({"loggedIn": False})
    return JSONResponse({"loggedIn": True, "uid": user["uid"], "name": user["name"], "is_admin": user["is_admin"]})


# ── Game Sync ──

@app.post("/api/sync")
async def sync(request: Request):
    user = await get_user(request)
    if not user:
        return JSONResponse({"error": "not logged in"}, status_code=401)
    uid = user["uid"]
    body = await request.json()

    if not check_rate_limit(f"sync:{uid}", 10):
        return JSONResponse({"ok": True, "pending": {}, "serverSettings": await get_server_settings()})

    gs = body.get("gameState")
    if gs is not None:
        await db_update("saves", {"game_state": gs}, f"uid=eq.{uid}")
        # Fire-and-forget ranking update
        asyncio.create_task(_update_ranking(uid, user["name"], body))

    pending: dict = {}
    try:
        us_res = await db_select("user_settings", "settings", f"uid=eq.{uid}")
        if us_res:
            settings = dict(us_res[0].get("settings") or {})
            pg = settings.pop("pending_give", None)
            if pg:
                pending = pg
                gs_res = await db_select("saves", "game_state", f"uid=eq.{uid}")
                if gs_res:
                    curr_gs = dict(gs_res[0].get("game_state") or {})
                    for k, v in pending.items():
                        curr_val = float(curr_gs.get(k, 0))
                        curr_gs[k] = curr_val + float(v)
                    await db_update("saves", {"game_state": curr_gs}, f"uid=eq.{uid}")
                await db_update("user_settings", {"settings": settings}, f"uid=eq.{uid}")
    except Exception as e:
        print(f"[ERROR] Pending give: {e}")

    return JSONResponse({"ok": True, "pending": pending, "serverSettings": await get_server_settings()})


async def _update_ranking(uid: str, name: str, body: dict):
    try:
        await db_upsert("rankings", {
            "uid": uid, "name": name,
            "combat_power": body.get("combatPower", 0),
            "level": body.get("level", 1), "stage": body.get("stage", 1),
            "knight_stage": body.get("knightStage", 0),
            "archer_stage": body.get("archerStage", 0),
            "rogue_stage": body.get("rogueStage", 0),
            "class_name": body.get("className", ""),
            "class_stage": body.get("classStage", ""),
        })
    except Exception as e:
        print(f"[ERROR] Rank update: {e}")


@app.post("/api/save")
async def save(request: Request):
    user = await get_user(request)
    if not user:
        return JSONResponse({"error": "not logged in"}, status_code=401)
    uid = user["uid"]
    body = await request.json()
    await db_update("saves", {"game_state": body.get("gameState", {})}, f"uid=eq.{uid}")
    await db_upsert("rankings", {
        "uid": uid, "name": user["name"],
        "combat_power": body.get("combatPower", 0),
        "level": body.get("level", 1), "stage": body.get("stage", 1),
        "knight_stage": body.get("knightStage", 0),
        "archer_stage": body.get("archerStage", 0),
        "rogue_stage": body.get("rogueStage", 0),
        "class_name": body.get("className", ""),
        "class_stage": body.get("classStage", ""),
    })
    return JSONResponse({"ok": True})


@app.get("/api/load")
async def load(request: Request):
    user = await get_user(request)
    if not user:
        return JSONResponse({"error": "not logged in"}, status_code=401)
    rows = await db_select("saves", "game_state", f"uid=eq.{user['uid']}")
    if rows:
        return JSONResponse({"gameState": rows[0].get("game_state")})
    return JSONResponse({"gameState": None})


@app.post("/api/reset")
async def reset(request: Request):
    user = await get_user(request)
    if not user:
        return JSONResponse({"error": "not logged in"}, status_code=401)
    uid = user["uid"]
    await db_delete("saves", f"uid=eq.{uid}")
    await db_delete("rankings", f"uid=eq.{uid}")
    await db_delete("user_settings", f"uid=eq.{uid}")
    return JSONResponse({"ok": True})


@app.post("/api/admin/reset-all")
async def reset_all(request: Request):
    user = await get_user(request)
    if not is_admin(user):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    saves = await db_select("saves", "uid", None)
    count = 0
    for s in saves:
        uid = s["uid"]
        await db_update("saves", {"game_state": {}}, f"uid=eq.{uid}")
        count += 1
    await db_delete("rankings", "id=gt.0")
    await db_delete("user_settings", "uid=neq.none")
    return JSONResponse({"ok": True, "count": count})


# ── Rankings ──

@app.get("/api/rankings")
async def rankings(tab: str = Query(default="combat_power")):
    valid = {"combat_power", "knight_stage", "archer_stage", "rogue_stage"}
    col = tab if tab in valid else "combat_power"
    rows = await db_select_ordered("rankings", "*", col, True, 50)
    return JSONResponse({"rankings": rows})


# ── Server Settings ──

@app.get("/api/server-settings")
async def server_settings():
    return JSONResponse(await get_server_settings())


@app.post("/api/server-settings")
async def set_server_settings(request: Request):
    user = await get_user(request)
    if not is_admin(user):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    body = await request.json()
    for k, v in body.items():
        await db_upsert("server_settings", {"key": k, "value": float(v)})
    global ss_cache_time
    ss_cache_time = 0
    return JSONResponse({"ok": True})


# ── User Settings ──

@app.get("/api/my-settings")
async def my_settings(request: Request):
    user = await get_user(request)
    if not user:
        return JSONResponse({})
    rows = await db_select("user_settings", "settings", f"uid=eq.{user['uid']}")
    if rows:
        return JSONResponse(rows[0].get("settings") or {})
    return JSONResponse({})


@app.get("/api/user-settings/{uid}")
async def get_user_settings(request: Request, uid: str = Path()):
    user = await get_user(request)
    if not is_admin(user):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    rows = await db_select("user_settings", "settings", f"uid=eq.{uid}")
    if rows:
        return JSONResponse(rows[0].get("settings") or {})
    return JSONResponse({})


@app.post("/api/user-settings/{uid}")
async def set_user_settings(request: Request, uid: str = Path()):
    user = await get_user(request)
    if not is_admin(user):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    body = await request.json()
    await db_upsert("user_settings", {"uid": uid, "settings": body})
    return JSONResponse({"ok": True})


# ── Admin ──

@app.get("/api/admin/users")
async def admin_users(request: Request):
    user = await get_user(request)
    if not is_admin(user):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    rows = await db_select("saves", "uid,name", None)
    return JSONResponse({"users": rows})


@app.post("/api/admin/give")
async def admin_give(request: Request):
    user = await get_user(request)
    if not is_admin(user):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    body = await request.json()
    uid = body["uid"]
    field = body["field"]
    amount = int(body.get("amount", 0))

    us_res = await db_select("user_settings", "settings", f"uid=eq.{uid}")
    settings = dict((us_res[0].get("settings") or {}) if us_res else {})
    pending = dict(settings.get("pending_give") or {})
    pending[field] = int(pending.get(field, 0)) + amount
    settings["pending_give"] = pending
    await db_upsert("user_settings", {"uid": uid, "settings": settings})
    return JSONResponse({"ok": True})


@app.post("/api/admin/give-all")
async def admin_give_all(request: Request):
    user = await get_user(request)
    if not is_admin(user):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    body = await request.json()
    field = body["field"]
    amount = int(body.get("amount", 0))

    saves_res = await db_select("saves", "uid", None)
    us_res = await db_select("user_settings", "uid,settings", None)
    us_map: dict[str, dict] = {}
    for r in us_res:
        us_map[r["uid"]] = dict(r.get("settings") or {})

    count = 0
    for r in saves_res:
        uid = r["uid"]
        settings = dict(us_map.get(uid) or {})
        pending = dict(settings.get("pending_give") or {})
        pending[field] = int(pending.get(field, 0)) + amount
        settings["pending_give"] = pending
        await db_upsert("user_settings", {"uid": uid, "settings": settings})
        count += 1
    return JSONResponse({"ok": True, "count": count})


# ── Beacon save ──

@app.post("/api/save-beacon")
async def save_beacon(request: Request, uid: str = Query(default="")):
    if not uid:
        return Response(status_code=204)
    rows = await db_select("saves", "uid,name", f"uid=eq.{uid}")
    if not rows:
        return Response(status_code=204)
    user = rows[0]
    body = await request.json()
    await db_update("saves", {"game_state": body.get("gameState", {})}, f"uid=eq.{uid}")
    await db_upsert("rankings", {
        "uid": uid, "name": user["name"],
        "combat_power": body.get("combatPower", 0),
        "level": body.get("level", 1), "stage": body.get("stage", 1),
        "knight_stage": body.get("knightStage", 0),
        "archer_stage": body.get("archerStage", 0),
        "rogue_stage": body.get("rogueStage", 0),
        "class_name": body.get("className", ""),
        "class_stage": body.get("classStage", ""),
    })
    return Response(status_code=204)


# ── Run ──

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=PORT)
