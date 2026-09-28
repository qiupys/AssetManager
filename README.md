# 资产管家 · Asset Manager

Apple 设计语言的单文件个人记账与资产负债管理应用。零依赖、离线可用，可选自托管后端实现多设备云同步。

**在线版**：https://asset-ledger-22018.app.workbuddy.host/

## 功能

- **资产负债表**：净资产 Hero、存量结构（资产/负债+年利率）、固定与临时现金流分组、月度收支、提醒与待办、一键分析
- **每日记账**：支出/收入/转账，自定义分类（内置「其他」），记住上次账户选择，收支明细 tab
- **账户管理**：财报式双列布局（左资产/右负债），列可折叠；债务还款冲减与「已结清」徽章；每月固定现金流维护（手动项 + 按余额×年利率自动计提的利息项）
- **多数据空间**：动态增删/改名/切换（我的/测试），数据相互独立，修改即时自动保存
- **多币种**：CNY/HKD/USD/EUR/JPY + 自定义币种，手动汇率 + 在线汇率自动拉取（open.er-api.com 主源、frankfurter.dev 备源，12h 静默刷新），跨币种折算与转账
- **设置/导出**：JSON 导出/导入/清空/示例数据（均只作用于当前空间）

配色遵循国内习惯：收入/资产为红，支出/欠款为绿。金额统一保留 2 位小数。

## 数据存储

纯前端模式数据存于浏览器 `localStorage`：

| 键 | 内容 |
|---|---|
| `asset_ledger_v2_spaces` | 数据空间列表 |
| `asset_ledger_v2@<space_id>` | 各空间记账数据 JSON |
| `asset_ledger_v2_profile` | 当前空间记忆 |
| `asset_ledger_ui` | 界面状态（折叠等，不进备份） |

## 可选自托管后端

`server/` 提供一个轻量 FastAPI 后端（注册/登录/按账号隔离的数据读写），用于多设备同步：

- 账号：PBKDF2-SHA256（12 万轮）加盐哈希，随机 256bit 会话 token（30 天过期）
- 数据：每账号一个 JSON blob，整包读写；`X-If-Updated` 乐观并发守卫，防止旧标签页覆盖新数据
- 管理：`ADMIN_PASSWORD` 环境变量启用管理员（仅账号列表与删除，不提供数据查看）

```bash
pip install -r requirements.txt
LEDGER_DB=./ledger.db ADMIN_PASSWORD=xxx python3 server/app.py   # 默认 0.0.0.0:8000
```

接口回归测试（34 项，覆盖注册/登录/鉴权/读写/注销/版本守卫/管理员）：

```bash
python3 server/test_api.py
```

> 注：前端当前发布版为纯本地存储形态；云同步后端的配套前端入口未在发布版中启用。

## 部署

任意静态托管直接部署 `index.html` 即可；或作为 Python HTTP 应用整体部署（同源出前端 + 后端，监听 `PORT` 环境变量）。

## 目录结构

```
index.html          # 应用全部前端（单文件，无构建）
requirements.txt    # 后端依赖（fastapi / uvicorn）
server/app.py       # FastAPI 后端（鉴权 + 数据存储 + 静态首页挂载）
server/test_api.py  # 接口级回归测试（34 项）
```
