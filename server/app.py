"""资产管家 · 轻量后端：账号鉴权 + 按账号隔离的记账数据存储。

数据模型：
  users     username / salt / hash (PBKDF2-SHA256, 120k 轮)
  sessions  token (随机 256bit) / username，30 天过期
  userdata  username -> 记账数据 JSON blob（前端整包读写，自动同步）
"""
import hashlib
import json
import os
import secrets
import sqlite3
import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

# 前端单文件与 app.py 同仓库；同源部署时 / 直接出 index.html
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX_HTML = os.path.join(ROOT, "index.html")

# 默认随项目存放（相对 app.py 所在目录）；本地/服务器可用 LEDGER_DB 覆盖
DB_PATH = os.environ.get(
    "LEDGER_DB",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "ledger.db"),
)
SESSION_TTL = 86400 * 30  # 30 天
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
# 管理员会话仅存内存（重启即失效），与普通用户会话完全隔离
_admin_tokens: set[str] = set()

app = FastAPI(title="Asset Ledger Auth Backend")
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
)


def conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH)
    c.execute(
        "CREATE TABLE IF NOT EXISTS users("
        "username TEXT PRIMARY KEY, salt TEXT, hash TEXT, created REAL)"
    )
    c.execute(
        "CREATE TABLE IF NOT EXISTS sessions("
        "token TEXT PRIMARY KEY, username TEXT, created REAL)"
    )
    c.execute(
        "CREATE TABLE IF NOT EXISTS userdata("
        "username TEXT PRIMARY KEY, blob TEXT, updated REAL)"
    )
    return c


def hash_pwd(pwd: str, salt_hex: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", pwd.encode("utf-8"), bytes.fromhex(salt_hex), 120_000
    ).hex()


def current_user(request: Request):
    h = request.headers.get("authorization", "")
    if not h.startswith("Bearer "):
        return None
    token = h[7:].strip()
    if not token:
        return None
    with conn() as c:
        row = c.execute(
            "SELECT username, created FROM sessions WHERE token=?", (token,)
        ).fetchone()
    if not row or time.time() - row[1] > SESSION_TTL:
        return None
    return row[0]


def err(msg: str, code: int):
    return JSONResponse({"error": msg}, status_code=code)


def valid_username(u: str):
    if not (2 <= len(u) <= 24):
        return "用户名须为 2-24 个字符"
    return None


def valid_password(p: str):
    if len(p) < 6:
        return "密码至少 6 位"
    if len(p) > 128:
        return "密码过长"
    return None


@app.get("/")
async def index():
    return FileResponse(INDEX_HTML)


@app.get("/api/health")
async def health():
    return {"ok": True}


@app.post("/api/register")
async def register(req: Request):
    try:
        b = await req.json()
    except Exception:
        return err("请求格式错误", 400)
    u = str(b.get("username", "")).strip()
    p = str(b.get("password", ""))
    if m := valid_username(u):
        return err(m, 400)
    if m := valid_password(p):
        return err(m, 400)
    salt = secrets.token_hex(16)
    try:
        with conn() as c:
            c.execute(
                "INSERT INTO users(username, salt, hash, created) VALUES(?,?,?,?)",
                (u, salt, hash_pwd(p, salt), time.time()),
            )
    except sqlite3.IntegrityError:
        return err("该账号已存在，请直接登录", 409)
    return await _issue_session(u)


@app.post("/api/login")
async def login(req: Request):
    try:
        b = await req.json()
    except Exception:
        return err("请求格式错误", 400)
    u = str(b.get("username", "")).strip()
    p = str(b.get("password", ""))
    with conn() as c:
        row = c.execute(
            "SELECT salt, hash FROM users WHERE username=?", (u,)
        ).fetchone()
    if not row or not secrets.compare_digest(hash_pwd(p, row[0]), row[1]):
        return err("账号或密码错误", 401)
    return await _issue_session(u)


async def _issue_session(u: str):
    token = secrets.token_hex(32)
    with conn() as c:
        c.execute("DELETE FROM sessions WHERE created < ?", (time.time() - SESSION_TTL,))
        c.execute(
            "INSERT INTO sessions(token, username, created) VALUES(?,?,?)",
            (token, u, time.time()),
        )
    return {"token": token, "username": u}


@app.post("/api/logout")
async def logout(req: Request):
    h = req.headers.get("authorization", "")
    if h.startswith("Bearer "):
        with conn() as c:
            c.execute("DELETE FROM sessions WHERE token=?", (h[7:].strip(),))
    return {"ok": True}


@app.delete("/api/account")
async def delete_account(req: Request):
    """注销账号：需密码验证；删除该账号的云数据、全部会话与账号本身。"""
    u = current_user(req)
    if not u:
        return err("登录已过期，请重新登录", 401)
    try:
        b = await req.json()
    except Exception:
        return err("请求格式错误", 400)
    p = str(b.get("password", ""))
    with conn() as c:
        row = c.execute(
            "SELECT salt, hash FROM users WHERE username=?", (u,)
        ).fetchone()
    if not row or not secrets.compare_digest(hash_pwd(p, row[0]), row[1]):
        return err("密码验证失败，账号未删除", 401)
    with conn() as c:
        c.execute("DELETE FROM userdata WHERE username=?", (u,))
        c.execute("DELETE FROM sessions WHERE username=?", (u,))
        c.execute("DELETE FROM users WHERE username=?", (u,))
    return {"ok": True, "deleted": u}


@app.get("/api/data")
async def get_data(req: Request):
    u = current_user(req)
    if not u:
        return err("登录已过期，请重新登录", 401)
    with conn() as c:
        row = c.execute(
            "SELECT blob, updated FROM userdata WHERE username=?", (u,)
        ).fetchone()
    if not row:
        return {"data": None, "updated": None}
    return {"data": row[0], "updated": row[1]}


def admin_ok(request: Request) -> bool:
    h = request.headers.get("authorization", "")
    if not h.startswith("Bearer "):
        return False
    return h[7:].strip() in _admin_tokens


@app.post("/api/admin/login")
async def admin_login(req: Request):
    """管理员验证：只下发内存态 token；未配置 ADMIN_PASSWORD 时禁用。"""
    if not ADMIN_PASSWORD:
        return err("管理员未配置（服务器需设置 ADMIN_PASSWORD 环境变量）", 503)
    try:
        b = await req.json()
    except Exception:
        return err("请求格式错误", 400)
    p = str(b.get("password", ""))
    if not secrets.compare_digest(p, ADMIN_PASSWORD):
        return err("管理密码错误", 401)
    tok = secrets.token_hex(32)
    _admin_tokens.add(tok)
    return {"token": tok}


@app.get("/api/admin/accounts")
async def admin_accounts(req: Request):
    """账号列表：仅用户名 / 注册时间 / 更新时间 / 占用空间，绝不返回记账数据。"""
    if not admin_ok(req):
        return err("管理员未验证或会话已失效", 401)
    with conn() as c:
        users = {
            r[0]: r[1]
            for r in c.execute("SELECT username, created FROM users").fetchall()
        }
        data = {
            r[0]: (r[1], r[2])
            for r in c.execute(
                "SELECT username, blob, updated FROM userdata"
            ).fetchall()
        }
    out = []
    for u, created in sorted(users.items(), key=lambda kv: kv[1] or 0):
        blob, updated = data.get(u, (None, None))
        out.append(
            {
                "username": u,
                "created": created,
                "updated": updated,
                "size": len(blob.encode("utf-8")) if blob else 0,
            }
        )
    return {"accounts": out, "count": len(out)}


@app.delete("/api/admin/account")
async def admin_delete_account(req: Request):
    """管理员删除指定账号（无需该账号密码）：删除账号、云数据与全部会话。"""
    if not admin_ok(req):
        return err("管理员未验证或会话已失效", 401)
    try:
        b = await req.json()
    except Exception:
        return err("请求格式错误", 400)
    u = str(b.get("username", "")).strip()
    if not u:
        return err("缺少用户名", 400)
    with conn() as c:
        if not c.execute(
            "SELECT 1 FROM users WHERE username=?", (u,)
        ).fetchone():
            return err("账号不存在", 404)
        c.execute("DELETE FROM userdata WHERE username=?", (u,))
        c.execute("DELETE FROM sessions WHERE username=?", (u,))
        c.execute("DELETE FROM users WHERE username=?", (u,))
    return {"ok": True, "deleted": u}


@app.put("/api/data")
async def put_data(req: Request):
    u = current_user(req)
    if not u:
        return err("登录已过期，请重新登录", 401)
    blob = (await req.body()).decode("utf-8")
    try:
        json.loads(blob)  # 只允许合法 JSON
    except Exception:
        return err("数据格式错误", 400)
    if len(blob) > 4 * 1024 * 1024:
        return err("数据过大", 413)
    # 乐观并发控制：客户端带上它所见的服务端版本；若服务端已被更新过则拒绝，
    # 防止启动期/旧标签页用陈旧快照覆盖较新的云端数据
    base = req.headers.get("x-if-updated")
    with conn() as c:
        row = c.execute(
            "SELECT updated FROM userdata WHERE username=?", (u,)
        ).fetchone()
        if row and base:
            try:
                if row[0] - float(base) > 1.0:
                    return JSONResponse(
                        {"error": "数据版本已过期，请刷新后重试", "server_updated": row[0]},
                        status_code=409,
                    )
            except ValueError:
                pass
        c.execute(
            "INSERT INTO userdata(username, blob, updated) VALUES(?,?,?) "
            "ON CONFLICT(username) DO UPDATE SET blob=excluded.blob, updated=excluded.updated",
            (u, blob, time.time()),
        )
    return {"ok": True, "updated": time.time()}


# 静态资源兜底（须注册在所有 /api 路由之后、__main__ 之前）：仅允许常规前端
# 扩展名，数据库等敏感文件永不外泄
_STATIC_EXTS = {".html", ".js", ".css", ".svg", ".png", ".ico", ".webmanifest"}


@app.get("/{fname}")
async def static_any(fname: str):
    safe = os.path.basename(fname)
    if os.path.splitext(safe)[1].lower() not in _STATIC_EXTS:
        return JSONResponse({"error": "not found"}, status_code=404)
    path = os.path.join(ROOT, safe)
    if not os.path.isfile(path):
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(path)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
