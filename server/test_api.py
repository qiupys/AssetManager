"""后端接口级回归：注册/登录/鉴权/数据读写/登出。"""
import os
import sys
import tempfile

os.environ["LEDGER_DB"] = tempfile.mktemp(suffix=".db")
sys.path.insert(0, "/Users/pengyu/WorkBuddy/每日记账/server")

from fastapi.testclient import TestClient  # noqa: E402
import app as ledger  # noqa: E402

c = TestClient(ledger.app)
fails = []


def check(name, cond, extra=""):
    print(("PASS" if cond else "FAIL"), name, extra)
    if not cond:
        fails.append(name)


# 1. 注册（弱密码拒绝 / 合法注册成功）
r = c.post("/api/register", json={"username": "pengyu", "password": "123"})
check("register 弱密码拒绝", r.status_code == 400, r.text)
r = c.post("/api/register", json={"username": "pengyu", "password": "secret66"})
check("register 成功返回 token", r.status_code == 200 and "token" in r.json(), r.text)
tok = r.json().get("token", "")

# 2. 重复注册
r = c.post("/api/register", json={"username": "pengyu", "password": "secret66"})
check("register 重复账号 409", r.status_code == 409, r.text)

# 3. 登录（错密码 / 对密码）
r = c.post("/api/login", json={"username": "pengyu", "password": "wrong12"})
check("login 错密码 401", r.status_code == 401, r.text)
r = c.post("/api/login", json={"username": "pengyu", "password": "secret66"})
check("login 成功", r.status_code == 200 and r.json().get("username") == "pengyu", r.text)

# 4. 未登录数据访问被拒
r = c.get("/api/data")
check("data 未鉴权 401", r.status_code == 401, r.text)
r = c.put("/api/data", content="{}", headers={"Authorization": "Bearer bad"})
check("data 假 token 401", r.status_code == 401, r.text)

# 5. 数据读写闭环
H = {"Authorization": f"Bearer {tok}"}
blob = '{"accounts":[{"id":"a1","name":"招行","balance":10000}],"txns":[{"id":"t1","amount":25}]}'
r = c.get("/api/data", headers=H)
check("data 初始为空", r.status_code == 200 and r.json()["data"] is None, r.text)
r = c.put("/api/data", content=blob, headers=H)
check("data PUT 成功", r.status_code == 200, r.text)
r = c.put("/api/data", content="not-json", headers=H)
check("data PUT 非法 JSON 400", r.status_code == 400, r.text)
r = c.get("/api/data", headers=H)
check("data GET 读回一致", r.json()["data"] == blob, r.text)

# 6. 账号隔离：第二个用户看不到第一个用户的数据
r = c.post("/api/register", json={"username": "test", "password": "secret66"})
tok2 = r.json()["token"]
r = c.get("/api/data", headers={"Authorization": f"Bearer {tok2}"})
check("data 账号隔离", r.json()["data"] is None, r.text)

# 7. 登出后 token 失效
r = c.post("/api/logout", headers=H)
check("logout ok", r.status_code == 200, r.text)
r = c.get("/api/data", headers=H)
check("logout 后 token 失效", r.status_code == 401, r.text)

# 8. 注销账号：需登录 + 密码验证；删除后账号/数据/会话全部清除
r = c.post("/api/login", json={"username": "pengyu", "password": "secret66"})
tok = r.json()["token"]
H = {"Authorization": f"Bearer {tok}"}
r = c.put("/api/data", content=blob, headers=H)
check("重新登录后 PUT 数据", r.status_code == 200, r.text)
r = c.request("DELETE", "/api/account", headers=H, json={"password": "wrong12"})
check("delete account 错密码 401", r.status_code == 401, r.text)
r = c.request("DELETE", "/api/account", headers={"Authorization": "Bearer bad"}, json={"password": "x"})
check("delete account 未鉴权 401", r.status_code == 401, r.text)
r = c.request("DELETE", "/api/account", headers=H, json={"password": "secret66"})
check("delete account 成功", r.status_code == 200 and r.json().get("deleted") == "pengyu", r.text)
r = c.get("/api/data", headers=H)
check("delete 后会话失效", r.status_code == 401, r.text)
r = c.post("/api/login", json={"username": "pengyu", "password": "secret66"})
check("delete 后账号不存在（登录 401）", r.status_code == 401, r.text)

# 9. 第二个用户不受影响
r = c.post("/api/login", json={"username": "test", "password": "secret66"})
check("其他账号不受影响", r.status_code == 200, r.text)

# 10. 版本守卫：陈旧基线的 PUT 被拒绝（防旧快照覆盖新数据）
import time as _t
r = c.post("/api/register", json={"username": "vtest", "password": "secret66"})
H3 = {"Authorization": f"Bearer {r.json()['token']}"}
c.put("/api/data", content='{"a":1}', headers=H3)
u1 = c.get("/api/data", headers=H3).json()["updated"]
_t.sleep(1.2)
c.put("/api/data", content='{"a":2}', headers=H3)  # 另一次写入推进服务端版本
r = c.put("/api/data", content='{"a":3}', headers={**H3, "X-If-Updated": str(u1)})
check("陈旧基线 PUT 409", r.status_code == 409, r.text)
r = c.put("/api/data", content='{"a":3}', headers={**H3, "X-If-Updated": str(u1 + 100)})
check("新鲜基线 PUT 200", r.status_code == 200, r.text)
r = c.put("/api/data", content='{"a":4}', headers=H3)  # 不带基线 → 直接放行（兼容旧客户端）
check("无基线 PUT 200", r.status_code == 200, r.text)

# 11. 管理员：未配置密码时禁用；配置后 验证/列表（不含数据内容）/删除 全闭环
os.environ["ADMIN_PASSWORD"] = "admin-pwd-123"
ledger.ADMIN_PASSWORD = "admin-pwd-123"
r = c.post("/api/admin/login", json={"password": "wrong"})
check("admin login 错密码 401", r.status_code == 401, r.text)
r = c.get("/api/admin/accounts")
check("admin accounts 未验证 401", r.status_code == 401, r.text)
r = c.post("/api/admin/login", json={"password": "admin-pwd-123"})
check("admin login 成功", r.status_code == 200 and "token" in r.json(), r.text)
atok = r.json()["token"]
AH = {"Authorization": f"Bearer {atok}"}
r = c.get("/api/admin/accounts", headers=AH)
check("admin accounts 列表", r.status_code == 200 and r.json()["count"] >= 2, r.text)
names = [a["username"] for a in r.json()["accounts"]]
check("admin accounts 含用户名不含数据", "vtest" in names and all("blob" not in a and "data" not in a for a in r.json()["accounts"]), r.text)
r = c.request("DELETE", "/api/admin/account", headers=AH, json={"username": "nope"})
check("admin delete 不存在 404", r.status_code == 404, r.text)
r = c.request("DELETE", "/api/admin/account", headers=AH, json={"username": ""})
check("admin delete 空用户名 400", r.status_code == 400, r.text)
r = c.request("DELETE", "/api/admin/account", headers=AH, json={"username": "vtest"})
check("admin delete 成功", r.status_code == 200 and r.json()["deleted"] == "vtest", r.text)
r = c.post("/api/login", json={"username": "vtest", "password": "secret66"})
check("admin delete 后该账号登录 401", r.status_code == 401, r.text)
r = c.request("DELETE", "/api/admin/account", headers={"Authorization": "Bearer bad"}, json={"username": "test"})
check("admin delete 假 token 401", r.status_code == 401, r.text)
ledger._admin_tokens.clear()
r = c.get("/api/admin/accounts", headers=AH)
check("admin token 清空后失效", r.status_code == 401, r.text)

print("\n%d passed, %d failed" % (34 - len(fails), len(fails)))
sys.exit(1 if fails else 0)
