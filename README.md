# General Personal Agent

> **第一次拿到这个仓库？按 [2. 快速开始](#2-快速开始零云凭证) 走一遍即可：克隆 → 准备 PostgreSQL → 写 9 行 `.env` → `make db` → `make build-wb` → `make run` → 浏览器登录发一条消息。约 10 分钟，全程不需要任何云厂商凭证。**

General Personal Agent（GPA）是一个以 **VeADK Personal Agent** 为主体的个人 Agent 平台：文本与语音是通道适配器，上下文管理内置在 Agent，MA 是可通过 MCP Task Gateway 调用的任务执行后端，前端只有一个「交互工作台」，文本/语音共用同一个 `conversationId`。

技术栈：Python 3.12 + FastAPI + PostgreSQL · React 18 + Vite 5 + TypeScript。

## 目录

- [1. 环境要求](#1-环境要求)
- [2. 快速开始（零云凭证）](#2-快速开始零云凭证)
- [3. 前端两种运行方式](#3-前端两种运行方式)
- [4. 配置说明](#4-配置说明)
- [5. 数据库运维](#5-数据库运维)
- [6. 验证与测试](#6-验证与测试)
- [7. 常见问题](#7-常见问题)
- [8. 项目结构与架构](#8-项目结构与架构)
- [9. API 一览](#9-api-一览)
- [10. 安全说明](#10-安全说明)
- [11. License](#11-license)

---

## 1. 环境要求

| 依赖 | 版本 / 说明 | 是否必须 |
|---|---|---|
| Python | `>=3.12,<3.14`（CI 用 3.12） | ✅ |
| `uv` | `make install` 依赖它；也可用普通 venv + pip 代替 | 二选一 |
| Node.js + npm | 20+（CI 用 22），需要 `npm ci` | ✅ 构建前端 |
| PostgreSQL | 14+（CI 用 16），监听 `127.0.0.1:5432` | ✅ |
| `psql` 命令行 | `make db`、迁移、测试都要调用它 | ✅ |
| Redis | 可选；`GPA_REDIS_URL` 留空则用进程内限流 | ❌ |
| Docker | 可选，用来一行起 PostgreSQL | ❌ |
| 云厂商凭证 | 仅「完整功能」需要，快速开始不需要 | ❌ |

操作系统：macOS / Linux。Windows 请用 WSL2（Makefile 依赖 `bash` / `make` / `psql`）。

安装 `psql` 客户端：

```bash
# macOS
brew install libpq && brew link --force libpq     # 或 brew install postgresql@16
# Ubuntu / Debian
sudo apt-get update && sudo apt-get install -y postgresql-client
```

---

## 2. 快速开始（零云凭证）

### 2.1 克隆

```bash
git clone <你的仓库地址> general-personal-agent
cd general-personal-agent
```

> 把 `<你的仓库地址>` 换成实际的 GitHub 仓库 URL（当前仓库尚未配置 remote）。

### 2.2 准备 PostgreSQL

⚠️ **`make db` 只执行 DDL，不会创建数据库和角色。** 先准备好 `demo` / `demo` 角色和 `general_personal_agent` 库（与 `.env` 和 `Makefile` 的默认值一致）。

**方式 A：Docker（推荐，与 CI 完全一致）**

```bash
docker run -d --name gpa-postgres \
  -e POSTGRES_USER=demo -e POSTGRES_PASSWORD=demo \
  -e POSTGRES_DB=general_personal_agent \
  -p 5432:5432 postgres:16

docker exec gpa-postgres pg_isready -U demo -d general_personal_agent
```

> 如果本机 5432 已被占用，先停掉原有 PostgreSQL，或映射其他端口并同步修改 2.3 的 `DATABASE_URL` 与 2.5 的 `DB=`。

**方式 B：已有本地 PostgreSQL**

用超级用户连接（macOS Homebrew 一般是 `psql postgres`；Linux 一般是 `sudo -u postgres psql`）：

```sql
CREATE ROLE demo WITH LOGIN PASSWORD 'demo';
CREATE DATABASE general_personal_agent OWNER demo;
```

验证连通：

```bash
psql "postgresql://demo:demo@127.0.0.1:5432/general_personal_agent" -c 'select 1'
```

看到 `1` 即成功。想用自己的库名/账号见 [5.3 自定义数据库连接](#53-自定义数据库连接)。

### 2.3 写 `.env`

仓库里**没有** `.env`（已被 `.gitignore` 忽略），只有模板 `.env.example`（含全部 149 个可选项）。初次运行只需下面 9 行——直接新建 `.env` 粘贴即可：

```dotenv
APP_SECRET_KEY=dev-only-change-me
DEMO_LOGIN_PIN=123456
DATABASE_URL=postgresql+asyncpg://demo:demo@127.0.0.1:5432/general_personal_agent
GPA_AGENT_MODE=stub
OV_USER_KEY_ADMIN=dev-placeholder
OV_USER_KEY_ALICE=dev-placeholder
OV_USER_KEY_BOB=dev-placeholder
MA_AGENT_ID=dev-placeholder
MA_ENVIRONMENT_ID=dev-placeholder
```

| 配置 | 作用 | 不填会怎样 |
|---|---|---|
| `APP_SECRET_KEY` | 会话 Cookie 签名密钥 | **启动直接失败**：`APP_SECRET_KEY must be set` |
| `DEMO_LOGIN_PIN` | 三个演示账号共用的登录 PIN（不存数据库） | 登录永远 401 |
| `DATABASE_URL` | asyncpg 连接串（注意 `+asyncpg`） | 连不上库 → 启动失败 |
| `GPA_AGENT_MODE=stub` | 确定性开发桩，不调用任何大模型 | 留 `auto` 时因没有 ARK key 也会自动回退到 stub |
| `OV_USER_KEY_*` | OpenViking 用户 key | **登录返回 409**（`no OpenViking key for ...`）；本地填任意非空占位值即可，登录本身不访问 OV |
| `MA_AGENT_ID` / `MA_ENVIRONMENT_ID` | MA 任务执行后端标识 | 仅影响 `make smoke-gpa`（报 `MA executor is not configured`）；占位值即可，不会真的调用 MA |

> `.env.example` 中标 `[必填]` 是指**完整云端功能**所需；按本文快速开始用占位值即可跑通。真实凭证见 [4. 配置说明](#4-配置说明)。生成随机密钥：`openssl rand -hex 32`。

### 2.4 安装后端依赖

```bash
make install
```

它执行 `uv venv .venv --python 3.12` + `uv pip install -e ".[dev]"`。没有 `uv` 也可以：

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
```

其余所有 `make` 目标只使用 `.venv/bin/python`，不再需要 uv。

### 2.5 初始化数据库

```bash
make db
```

依次应用 `db/schema.sql` → `002_v05.sql` → `003..018`，最终得到 25 张表，并写入 3 个演示账号。验证：

```bash
psql "postgresql://demo:demo@127.0.0.1:5432/general_personal_agent" \
  -Atc "select count(*) from information_schema.tables where table_schema='public' and table_type='BASE TABLE'"
# 25
psql "postgresql://demo:demo@127.0.0.1:5432/general_personal_agent" \
  -Atc "select string_agg(app_user_id, ',' order by app_user_id) from app_user"
# admin,alice,bob
```

> `make db` 是**一次性**的：若 `schema_migrations` 已存在会拒绝执行，升级请用 `make db-migrate`。详见 [5. 数据库运维](#5-数据库运维)。

### 2.6 构建前端

```bash
make build-wb          # 等价于 cd web-workbench && npm ci && npm run build
```

产物在 `web-workbench/dist/`。**必须先构建再启动后端**：FastAPI 在导入时就决定是否挂载 `/assets`。

### 2.7 启动

```bash
make run
```

看到 `Application startup complete.` 和 `Uvicorn running on http://127.0.0.1:8100` 即成功。

> 启动日志里出现 `tts warmup failed: ...` 是**正常**的——未配置语音凭证时那次预热请求会失败，服务照常启动。

### 2.8 打开浏览器

访问 **http://127.0.0.1:8100/**，选择账号（`admin` / `alice` / `bob` 任意一个），输入 2.3 里设置的 PIN（`123456`）。进入工作台后发一条 `hello`，会收到 `收到：hello`——这是 stub 模式的确定性回复，说明从浏览器到 Agent 的整条链路已通。

交互式 API 文档：http://127.0.0.1:8100/docs

### 2.9 命令行自检（推荐）

另开一个终端：

```bash
# 1. 健康检查（公开，不需要凭证）
curl -s http://127.0.0.1:8100/health | head -c 200

# 2. 演示账号
curl -s http://127.0.0.1:8100/api/demo/users

# 3. 登录并保存 Cookie
curl -s -c /tmp/gpa.cookies -X POST http://127.0.0.1:8100/api/auth/login \
  -H 'Content-Type: application/json' -d '{"user_id":"alice","pin":"123456"}'

# 4. 建立会话
CID=$(curl -s -b /tmp/gpa.cookies -X POST http://127.0.0.1:8100/api/conversations \
  -H 'Content-Type: application/json' -d '{"title":"hello","channel":"text"}' \
  | .venv/bin/python -c "import sys,json; print(json.load(sys.stdin)['conversationId'])")

# 5. 发消息（stub 回显）
curl -s -b /tmp/gpa.cookies -X POST "http://127.0.0.1:8100/api/conversations/$CID/messages" \
  -H 'Content-Type: application/json' -d '{"content":"hello"}'

# 6. 时间线里应出现 "收到：hello"
curl -s -b /tmp/gpa.cookies "http://127.0.0.1:8100/api/conversations/$CID/timeline"
```

不启动服务也能验证核心链路（直连数据库 + stub Agent）：

```bash
make smoke-gpa         # 输出 "P1 smoke PASS"
```

至此项目已完整跑通 🎉

---

## 3. 前端两种运行方式

| 方式 | 命令 | 访问地址 | 说明 |
|---|---|---|---|
| 生产构建（推荐） | `make build-wb` 然后 `make run` | http://127.0.0.1:8100/ | FastAPI 直接托管 `web-workbench/dist`，同源，无需代理 |
| Vite 开发模式 | 终端 A `make run`；终端 B `cd web-workbench && npm run dev` | http://127.0.0.1:5173/ | 热更新；`vite.config.ts` 把 `/api` 和 `/ws` 代理到 `127.0.0.1:8100`，SSE / WebSocket 都可用 |

- 前端无需任何环境变量（请求使用相对路径 + `credentials: same-origin`）。
- `npm run preview` 只提供静态预览、**没有配置代理**，无法连后端，不要用它验证整链路。
- 后端换端口：`make run PORT=8123`。注意 `APP_PORT` 写在 `.env` 里只影响 `python -m general_personal_agent.main`，**不影响 `make run`**；Vite 代理目标写死为 `8100`，换端口需同步改 `web-workbench/vite.config.ts`。
- API / 前端地址一览：后端与生产 SPA `8100`、Vite dev `5173`、MCP 网关 `8201`、PostgreSQL `5432`。

---

## 4. 配置说明

### 4.1 三档配置

| 档位 | 需要什么 | 能得到什么 |
|---|---|---|
| A. 最小可启动 | `APP_SECRET_KEY` + `DATABASE_URL` + PostgreSQL + `make db` | `/health`、SPA、API 文档；**无法登录** |
| B. 可登录跑通（本文快速开始） | A + `DEMO_LOGIN_PIN` + `GPA_AGENT_MODE=stub` + 3 个 `OV_USER_KEY_*` 占位值 | 浏览器登录、文本会话（stub 回显）、`make smoke-gpa` |
| C. 完整功能 | B + 下表真实凭证 | 真实模型对话、语音、任务执行、文件/知识库、联网搜索 |

`.env.example` 是完整模板，按编号分节：1 应用基础 / 2 方舟模型 / 3 语音 / 4 OpenViking / 5 登录态 / 6 Postgres / 7 Managed Agents / 8 TOS / 9 搜索 / 10 知识库 / 11 观测 / 12 GPA-native core / 文件上传 / A2A / service JWT。

### 4.2 按功能分组

| 功能 | 需要的环境变量 | 不配置的后果 |
|---|---|---|
| 真实模型对话（VeADK） | `ARK_API_KEY` + `MODEL_AGENT_NAME` | `auto` 回退 stub；强制 `veadk` 会失败 |
| 语音 ASR / TTS | `TOOL_VESPEECH_APP_ID` + `TOOL_VESPEECH_API_KEY` + `TOOL_VESPEECH_SPEAKER` | 语音页不可用；启动时有一条预热失败警告 |
| MA 任务执行 | `MA_AGENT_ID` + `MA_ENVIRONMENT_ID` + `MA_API_KEY`（或复用 `ARK_API_KEY`） | 任务提交报 `MA executor is not configured` |
| 文件 / 知识库 / 产物（TOS） | `VOLCENGINE_ACCESS_KEY` + `VOLCENGINE_SECRET_KEY`（+ `VOLCENGINE_SESSION_TOKEN`）+ `DATABASE_TOS_BUCKET_NAME` | 上传 / 下载 / 产物不可用 |
| OpenViking 记忆与知识库 | 真实的 `OV_USER_KEY_ADMIN` / `ALICE` / `BOB` | 检索降级、写回失效；占位值只够登录 |
| 联网搜索 | `ARK_API_KEY` 或 `VOLCENGINE_ACCESS_KEY` / `SECRET_KEY`（且需 veadk 模式） | 搜索工具不可用 |
| Redis 限流 | `GPA_REDIS_URL` | 留空 → 进程内限流（功能正常） |
| 内部 service JWT / OAuth | `GPA_SERVICE_JWT_SECRET` 或 `GPA_SERVICE_TOKEN`（+ OAuth client） | `/api/internal/*` 返回 503 |
| A2A 出站 Executor | `A2A_ENABLED=true` + `A2A_AGENT_URL` + `A2A_TOKEN` + `A2A_ALLOWED_HOSTS` | 默认关闭；开启时强制白名单 |

> 所有真实密钥只写在本机 `.env`；该文件已被 `.gitignore` 忽略，模板 `.env.example` 不含任何真实值。

---

## 5. 数据库运维

### 5.1 `make db` / `make db-migrate`

| 命令 | 用途 |
|---|---|
| `make db` | **仅用于全新空库**：`schema.sql` → `002` → `003..018`，一次性 |
| `make db-migrate` | 已初始化的库升级：按 `schema_migrations` 跳过已应用迁移，可重复执行 |

- `db/schema.sql` 是上一代项目的**历史基线**，不是最终表结构；`007_drop_legacy.sql` 会删掉旧表，最终收敛为 25 张 GPA-native 表。**不要只跑 `schema.sql`**。
- `002` / `003` / `004` 早于 `schema_migrations` 表（由 `005` 创建），因此不自我登记；`db-migrate` 里有针对它们的跳过规则，请勿手工重复执行。
- `make db` 检测到 `public.schema_migrations` 已存在会打印 `database already bootstrapped` 并退出，属正常保护。

### 5.2 测试数据库

`make test` 默认只跑非 PG 测试；要跑全部集成测试，需要一个**与应用库不同**的已存在数据库：

```bash
psql postgres -c 'CREATE DATABASE general_personal_agent_test OWNER demo'
TEST_DATABASE_URL='postgresql+asyncpg://demo:demo@127.0.0.1:5432/general_personal_agent_test' make test
```

`tests/conftest.py` 会在该库里建随机 schema、套用全部迁移、测完删除；不碰应用库，也不需要 `make db`。CI 正是这么做的（PostgreSQL 16 + 独立测试库）。

### 5.3 自定义数据库连接

两种 DSN 写法**不要混用**：

- `DATABASE_URL`（`.env`，给 asyncpg）：`postgresql+asyncpg://user:pw@host:port/db`
- `DB`（Makefile，给 psql）：`postgresql://user:pw@host:port/db`（**没有 `+asyncpg`**）

```bash
# 假设已用超级用户建好 gpa_user / gpa_db
make db DB='postgresql://gpa_user:change-me@127.0.0.1:5432/gpa_db'
# 并把 .env 改为：
# DATABASE_URL=postgresql+asyncpg://gpa_user:change-me@127.0.0.1:5432/gpa_db
```

升级已初始化的自定义库：`make db-migrate DB='postgresql://gpa_user:change-me@127.0.0.1:5432/gpa_db'`。

---

## 6. 验证与测试

| 命令 | 作用 | 需要真实云凭证 |
|---|---|---|
| `make lint` | `ruff check src tests scripts` | ❌ |
| `make fmt` | `ruff format` + `ruff check --fix` | ❌ |
| `make test` | pytest（未设 `TEST_DATABASE_URL` 时跳过 PG 集成测试） | ❌ |
| `make smoke-gpa` | stub Agent 核心链路（会话 / 事件 / 时间线 / trace / task），直连 DB，无需启动服务 | ❌ |
| `make build-wb` | 安装前端依赖并构建 dist | ❌ |
| `make test-wb-ui` | Playwright UI e2e（需服务 + dist + PIN） | ❌ |
| `make smoke` | ARK / OV / TTS / ASR / MA / TOS 全链路连通性 | ✅ |
| `make e2e-voice-gpa` | 真实语音链路（需服务 :8100 + `DEMO_LOGIN_PIN`） | ✅ |
| `make e2e-task-gpa` | 真实 MA 任务链路 | ✅ |
| `make e2e-artifact-tos` | 真实 TOS copy / range | ✅ |
| `make service-token` | 签发内部 service token（需 `GPA_SERVICE_JWT_SECRET`） | ❌ |
| `make mcp-server` | 独立 MCP 网关 `127.0.0.1:8201/mcp` | 执行任务时 ✅ |
| `make clean` | 清理缓存与构建产物 | ❌ |

Playwright UI e2e（后端在 :8100 且前端已构建）：

```bash
cd web-workbench
npx playwright install chromium
WB_E2E_PIN=123456 npm run test:e2e
```

CI（`.github/workflows/ci.yml`）在 Python 3.12 + Node 22 + PostgreSQL 16 上跑 lint、全量 pytest（含 PG 集成）、`tsc`、`vite build` 和三个 Playwright 用例，可作为「标准可运行环境」的参照。

---

## 7. 常见问题

**Q：启动报 `APP_SECRET_KEY must be set; refusing to start...`**
`.env` 不存在或 `APP_SECRET_KEY` 为空。`.env` 不会随仓库分发，必须自己创建；见 2.3。

**Q：登录一直 401 `INVALID_CREDENTIALS`**
`DEMO_LOGIN_PIN` 为空或输入不一致。PIN 是环境变量，不在数据库里；改完 `.env` 要重启服务。

**Q：登录返回 409 `no OpenViking key for alice (key_ref=OV_USER_KEY_ALICE)`**
登录会解析用户的 OV 身份，要求对应 `OV_USER_KEY_*` 非空（**这一步不访问 OV 服务**）。本地填任意非空占位值即可；真实记忆 / 知识库功能才需要真实 key。见 2.3。

**Q：打开 `/` 显示「交互工作台尚未构建」/ HTTP 503**
前端未构建。执行 `make build-wb` 后**重启**后端（`/assets` 挂载在进程导入时决定），或改用 Vite dev 访问 :5173。

**Q：`make db` 提示 `database already bootstrapped`**
库已初始化过，改用 `make db-migrate`。

**Q：`psql: command not found`**
缺 PostgreSQL 客户端，见第 1 节的安装命令。

**Q：启动日志有 `tts warmup failed`**
未配置语音凭证时的正常警告，不影响启动和其他功能；配置 `TOOL_VESPEECH_*` 后消失。

**Q：启动失败 / `connection refused` / `role "demo" does not exist`**
PostgreSQL 没起，或角色 / 库没建。`make db` 不创建库；回到 2.2 检查 `psql "postgresql://demo:demo@127.0.0.1:5432/general_personal_agent" -c 'select 1'` 能否通过。

**Q：`make smoke-gpa` 报 `MA executor is not configured`**
任务网关要求 `MA_AGENT_ID` / `MA_ENVIRONMENT_ID` 非空；快速开始里的占位值即可，不会真正调用 MA。

**Q：8100 端口被占用**
`make run PORT=8123` 换端口；注意 Vite dev 代理写死指向 8100，需同步修改 `web-workbench/vite.config.ts`。

**Q：改了 `.env` 没生效**
`.env` 只在启动时读取且配置有缓存，必须重启服务。环境变量优先级高于 `.env`，也可用 `GPA_AGENT_MODE=stub make run` 临时覆盖。

---

## 8. 项目结构与架构

```text
general-personal-agent/
├── src/general_personal_agent/   # 后端（gpa/ = 核心；api/ clients/ 为通道与外部客户端）
├── tests/                        # 测试（契约 / 集成 / 回归）
├── scripts/                      # 冒烟 / 验收 / 运维脚本
├── db/                           # schema 历史基线 + migrations 增量迁移
├── web-workbench/                # React + Vite 交互工作台（dist/ 为构建产物）
├── pyproject.toml
├── .env.example                  # 完整环境变量模板（本机 .env 不入库）
├── Makefile                      # 全部常用命令入口
└── .github/workflows/ci.yml      # 标准可运行环境参照
```

```text
文本/语音 Adapter
      ↓
ConversationService
      ↓
Personal Agent (VeADK) + ContextManager
      ↓
MCP Task Gateway → MaExecutor → MA
      ↑
TaskEventInbox → conversation events
```

核心边界：

- Conversation 是主模型，Task 是子资源；
- ContextManager 只属于 Agent；
- 任务结果经 TaskEventInbox 回 conversation；
- 语音与 Agent 同机；真 cancel；
- Agent 同进程 + MCP HTTP；A2A outbound Executor 已落地（默认关闭），A2A 入站 Server 后置；
- `db/schema.sql` 是历史基线，全新空库先建它，再经 `002..018` 迁移（其中 `007` 删除旧表）收敛为 GPA-native schema；不直接复用旧库数据。

不在 MVP：A2A 入站 Server / Task Agent、Local Executor、主动任务播报、独立子域 / DNS / TLS、第二套语音工作台。

---

## 9. API 一览

除标注为公开的接口外，均需登录 Cookie（`gpa_session`）或 service token。交互式文档见 http://127.0.0.1:8100/docs 。

```text
# 认证
POST   /api/auth/login                 # 共享 PIN 登录，Set-Cookie
POST   /api/auth/logout
GET    /api/auth/me

# 会话 / 时间线 / 实时事件
POST   /api/conversations
GET    /api/conversations
GET    /api/conversations/{id}
PATCH  /api/conversations/{id}
DELETE /api/conversations/{id}
GET    /api/conversations/{id}/timeline
GET    /api/conversations/{id}/context-traces
POST   /api/conversations/{id}/messages      # 202 + eventsUrl
GET    /api/conversations/{id}/events        # SSE
POST   /api/conversations/{id}/stop
POST   /api/conversations/{id}/memory/commit # 202，提交会话记忆
GET    /api/conversations/{id}/files
POST   /api/conversations/{id}/files
DELETE /api/conversations/{id}/files/{conversationFileId}
WS     /ws/session?conversationId=           # 16k PCM 上行 / 24k PCM 下行

# 任务与产物
GET    /api/tasks/{id}
POST   /api/tasks/{id}/cancel
GET    /api/tasks/{id}/artifacts
GET    /api/tasks/{id}/artifacts/{artifactId}/download

# 文件
POST   /api/files                      # 上传
GET    /api/files
GET    /api/files/{fileId}
GET    /api/files/{fileId}/download
POST   /api/files/{fileId}/reparse
POST   /api/files/{fileId}/promote
DELETE /api/files/objects/{fileId}
DELETE /api/files/{artifactId}        # 删除产物记录

# 知识库 / 记忆 / 上下文
GET    /api/kb/entries
GET    /api/kb/content
DELETE /api/kb/entries
GET    /api/kb/deletions
GET    /api/kb/documents/{docId}/download
POST   /api/kb/documents/{docId}/reindex
POST   /api/kb/documents/{docId}/retry-delete
GET    /api/memories
GET    /api/memories/content
GET    /api/context/tree
GET    /api/context/content

# 内部网关（service token）
POST   /api/internal/tasks
GET    /api/internal/tasks/{id}
POST   /api/internal/tasks/{id}/cancel
POST   /api/internal/oauth/token      # client credentials 换 service JWT
POST   /api/internal/oauth/revoke     # 吊销 service JWT

# 系统
GET    /health                       # 公开
GET    /api/demo/users               # 公开，演示账号列表
GET    /api/metrics/summary          # 需登录
```

MCP Task Gateway（独立进程）与 service token：

```bash
make service-token TENANT=demo SUBJECT=local-worker USERS=alice
make mcp-server                  # 127.0.0.1:8201/mcp
```

---

## 10. 安全说明

这是本地 / 演示级实现，公开部署前请注意：

- 登录使用单一 `DEMO_LOGIN_PIN`（所有账号共用），Cookie 默认不带 `Secure`；生产环境应替换为真正的用户体系与 SSO。
- 所有密钥（方舟、语音、OV、TOS、service JWT 等）需自行填入本机 `.env`，模板 `.env.example` 不含任何真实值；`APP_SECRET_KEY` 为空时服务拒绝启动。
- A2A 出站 Executor 默认关闭（`A2A_ENABLED=false`）。开启前请配置 `A2A_ALLOWED_HOSTS` 白名单；任务自带或远端返回的 URL 必须通过校验，私网 / 云元数据地址一律拒绝，且配置的 `A2A_TOKEN` 只会发给白名单内配置的 agent。
- `/health` 与 `/api/demo/users` 为公开端点；其余接口均需登录或 service token。`/health` 会回显凭证指纹前缀，请勿在不受信网络暴露。

---

## 11. License

本项目（含 `web-workbench/` 前端）整体采用 MIT 许可证，全文见仓库根目录 `LICENSE`。
