# General Personal Agent

> 独立项目 · 2026-10（设计 v1.0）
> **P0–P5 + C2 + 生产增强：GPA-native 工作台、语音、任务、记忆/KB、A2A Executor、JWT/KMS 文件/吊销、Redis、MCP HTTP OAuth/mTLS、artifact TTL/Range。**

General Personal Agent（GPA）是一个以 **VeADK Personal Agent** 为主体的个人 Agent 平台：

- 文本/语音是通道适配器；
- 上下文管理内置在 Agent；
- MA 是可通过 MCP Task Gateway 调用的任务执行后端；
- 前端只有一个“交互工作台”，文本/语音共用一个 `conversationId`。

本项目代码、数据库与版本历史独立演进。

---

## 当前状态

| 项 | 状态 |
|---|---|
| Python 包 | `general_personal_agent` |
| 数据库 | PostgreSQL；`db/schema.sql` 基线 + `db/migrations/` 增量迁移（当前到 018） |
| 本地后端 | `http://127.0.0.1:8100/` |
| 代码 | P0–P5 + C2 已落地：GPA-native schema、Conversation API/SSE、ContextManagerPlugin、Task Gateway/MaExecutor、OV 记忆/KB、文件与知识库、React 文本/语音工作台 |
| 前端 | React 单壳根路径 `/`：`/interact/text`、`/interact/voice`（按住说话/打断）、`/history`、`/tasks/:taskId`（可取消）、`/files`、`/knowledge`、`/context` |

---

## 架构

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
- Agent 同进程 + MCP HTTP；A2A outbound Executor 已落地，A2A 入站 Server 后置；
- GPA-native schema，不复用历史基线库。

---

## 仓库结构

```text
general-personal-agent/
├── src/general_personal_agent/   # 后端（gpa/ = 核心；api/ clients/ 为通道与外部客户端）
├── tests/                        # 测试（契约/集成/回归）
├── scripts/                      # 冒烟/验收/运维脚本
├── db/                           # schema 基线 + migrations 增量迁移
├── web-workbench/                # React + Vite 交互工作台
├── pyproject.toml
├── .env.example
└── Makefile
```

---

## 本地开发

> 当前 `.env` 为本地开发配置；不要提交真实密钥。

```bash
cd general-personal-agent
make install          # uv venv + 依赖
make db-migrate       # 增量 GPA-native schema（最新 018；legacy 表已由 007 删除）
make run              # 127.0.0.1:8100
```

无模型 key / CI 可用确定性开发桩：

```bash
GPA_AGENT_MODE=stub make run
```

后端 API：

```text
POST /api/conversations
GET  /api/conversations
GET  /api/conversations/{id}
GET  /api/conversations/{id}/timeline
GET  /api/conversations/{id}/context-traces
WS   /ws/session?conversationId=      # 16k PCM 上行 / 24k PCM 下行
POST /api/conversations/{id}/messages      # 202 + eventsUrl
GET  /api/conversations/{id}/events        # SSE
POST /api/conversations/{id}/stop
GET  /api/tasks/{id}
```

P1 冒烟（不调用真实模型）：

```bash
make smoke-gpa
```

P3 语音链路（合成语音 -> 真实 ASR/Agent/TTS，需要真实凭证）：

```bash
make e2e-voice-gpa
make e2e-task-gpa
```

MCP Task Gateway（独立进程）与 service token：

```bash
make service-token TENANT=demo SUBJECT=local-worker USERS=alice
make mcp-server          # 127.0.0.1:8201/mcp
make e2e-artifact-tos    # 真实 TOS copy/range（需有效 STS）
```

前端：

```bash
cd web-workbench
npm ci
npm run dev           # http://127.0.0.1:5173/（代理 API/SSE 到 8100）
npm run build         # 构建到 dist，FastAPI 根路径托管
```

Playwright（需 `DEMO_LOGIN_PIN` 一致的 `WB_E2E_PIN`）：

```bash
WB_E2E_PIN=<pin> npx playwright test
```

---

## 不在 MVP

- A2A 入站 Server/Task Agent；
- Local Executor；
- 主动任务播报；
- 独立子域/DNS/TLS；
- 第二套语音工作台。

---

## License

见仓库根目录 `LICENSE`。`web-workbench/` 内第三方组件归属见 `NOTICE` 与 `THIRD_PARTY_LICENSE_MAWORKER`。
