# EDUPLUS 工具

用于查询 EDUPLUS 课程与作业/实验状态、查找并下载课件（PPT/PPTX）、抓取导出作业内容（JSON/Markdown），同时提供命令行、网页界面、HTTP MCP 服务和 Telegram 提醒。

## 目录说明

```text
eduplus_tools/
  cli/
    main.py     # 统一命令行入口
  core/
    config.py   # config.json、环境变量与命令行配置解析
    client.py   # U+ API 请求与 Cookie/Header
  features/
    ppt.py      # PPT/PPTX 课件检索与下载
    homework.py # 作业抓取、JSON 保存、Markdown 转换
    status.py   # 课程、作业与实验状态抓取及筛选
  mcp/
    server.py   # 通用 MCP Server（Streamable HTTP / SSE / stdio）
  monitor/      # 定时扫描、快照差异与 SQLite 存储
  notify/       # Telegram 提醒 bot（通过 HTTP 消费状态 API）
  web/
    server.py   # 一体化 Web UI + 状态 API + MCP HTTP 服务入口
    static/     # HTML/CSS/JS 静态资源

config.json          # 本地真实配置，不要提交
config.example.json  # 配置模板
downloads/
  courseware/        # PPT/PPTX 输出
  homework/
    json/
      做过/
      没做过/
    markdown/
      做过/
      没做过/
```

## 配置与权限

复制配置模板，填写登录凭据 `session`；也可以直接通过环境变量（例如 Docker `.env`）配置：

```bash
cp config.example.json config.json
```

`session` 字段、`--session` 参数和 `EDUPLUS_SESSION` 环境变量应填入登录后 `www.eduplus.net` 浏览器开发者工具中 **Local Storage → AccessToken** 的值。请求通过 `x-access-token` 头鉴权，只复制 `SESSION` Cookie 不足以访问接口。凭据失效后，在浏览器重新登录再更新即可；也可以用 MCP 的 `configure_session` 工具在线更新，新值保存在数据目录的 `credentials.json`，重启后仍然有效。

配置优先级：

```text
命令行/请求参数 > 运行时更新 > --config-json > 在线更新并保存的凭据 (data/credentials.json) > 环境变量 (EDUPLUS_*) > config.json > 默认值
```

`credentials.json` 会记下保存时 `EDUPLUS_SESSION` 的指纹。之后如果你修改了 `.env` 里的 `EDUPLUS_SESSION`，以新的环境变量为准。

公共模式的网页任务只使用访客自己填写的 SESSION，不读取以上任何服务端凭据。

支持的常用环境变量：

```text
EDUPLUS_SESSION=        # AccessToken（CLI / Web / Status API / MCP 通用）
EDUPLUS_COURSE_ID=      # 默认课程 ID（可选）
EDUPLUS_BASE_URL=https://www.eduplus.net
EDUPLUS_OUTPUT=downloads
EDUPLUS_CONFIG=config.json
EDUPLUS_DATA_DIR=data   # 持久化状态目录：credentials.json、mcp-token
```

`config.json`、`downloads/` 与 `data/` 已加入 `.gitignore`，不要提交自己的真实凭据。

## 启动方式（Web UI + HTTP MCP 一体化服务）

网页界面、状态 API 和 **Streamable HTTP MCP** 默认集成在同一服务端口（`8000`）中运行：

```bash
# 本地启动（如使用 start_webui.sh 会自动创建 .venv 并安装 requirements.txt）
bash start_webui.sh

# 或手动运行
python3 -m eduplus_tools.web --host 127.0.0.1 --port 8000
```

Docker 部署（推荐）：

```bash
mkdir -p downloads data   # 先自己创建，避免 Docker 以 root 身份创建导致容器内无法写入
docker compose up -d --build
```

compose 默认只把端口绑定到宿主机的 `127.0.0.1`，外部经同一台机器上的 Tunnel 或反向代理访问。确实需要局域网直连时，设置 `EDUPLUS_BIND=0.0.0.0`。

**公网部署请关闭网页界面**：设置 `EDUPLUS_WEB_UI_ENABLED=false`。网页界面没有登录，本地模式又会使用服务端的 SESSION，暴露在公网上等于把你的账号交给任何知道地址的人。关闭后只保留 `/api/health`、`/api/v1`（状态 API，需要 `EDUPLUS_STATUS_TOKEN`）和 `/mcp`（需要 MCP token）。用 Tunnel 暴露时，建议在路由上限定路径 `^/mcp`，只公开 MCP。

Docker 与 Web 服务默认全面开放本地输出与服务端配置能力：
- `EDUPLUS_ENABLE_LOCAL_OUTPUT=true`：默认允许本地输出模式落盘到 `downloads/`
- `EDUPLUS_AUTO_DELETE_PUBLIC_DOWNLOADS=false`：默认保留任务下载文件（仍可按 `EDUPLUS_PUBLIC_JOB_TTL_SECONDS` 控制清理）
- `EDUPLUS_MCP_ENABLED=true`：默认在 `/mcp` 挂载通用 **MCP Streamable HTTP** 端点
- Web 表单留空 `SESSION` 时，只有本地模式会复用服务端配置的凭据；公共模式必须填写访客自己的 SESSION

## 通用 MCP 服务（HTTP）

当你在远程主机 / TV Box 的 Docker 容器中运行 `eduplus-web` 后，服务会自动暴露标准 MCP Streamable HTTP 端点：

- **默认地址**：`http://127.0.0.1:8000/mcp`
- **鉴权（默认开启）**：所有 MCP 请求都要带 `Authorization: Bearer <token>`。
  - 设置了 `EDUPLUS_MCP_TOKEN` 时使用它。
  - 未设置时，首次启动自动生成 token，保存到 `data/mcp-token`（权限 600），并在启动日志里打印一次；之后重启沿用同一个。查看：`cat data/mcp-token`。
  - 只有在上游已做访问控制（如 Cloudflare Access）时，才设置 `EDUPLUS_MCP_ALLOW_NO_AUTH=true` 关闭鉴权。
- **Host 白名单（防 DNS rebinding）**：始终允许 `localhost` / `127.0.0.1` / `[::1]`。通过 Tunnel 或反向代理用域名访问时，把域名加进 `EDUPLUS_MCP_ALLOWED_HOSTS`（逗号分隔，如 `mcp.example.com`），否则返回 421。设为 `*` 会关闭这项检查。
- **文件工具的范围**：`list_downloads` / `read_download` / `download_courseware` / `fetch_homework` 只能访问 `downloads/`，并排除 `web-jobs/`、`web-bundles/`、`status/` 和数据目录，避免读到其他网页用户的任务文件或服务端状态。
- **独立运行 MCP**（如不需 Web UI 或需单独端口）：

```bash
python3 -m eduplus_tools.mcp --host 127.0.0.1 --port 8001 --path /mcp
```

独立运行时的鉴权和 Host 规则同上；`--transport stdio` 不走 HTTP，不需要 token。

### 暴露的 MCP Tools 与 Resources

| Tool | 作用 |
|---|---|
| `check_session` | 检查当前 EDUPLUS 登录态（AccessToken）是否有效 |
| `configure_session` | 在线更新服务端 `session`（AccessToken）和默认 `course_id`，并保存到数据目录的 `credentials.json`（重启后仍有效） |
| `list_courses` | 列出课程及其 `course_id`、学期与开结课状态（支持 `all_courses=true`） |
| `list_tasks` | 查询各课程作业和实验的提交状态、截止时间、逾期情况 |
| `list_courseware` | 检索某门课的课件章节、文件名、大小及签名下载链接（默认 PPT/PPTX，`file_types` 可指定 `pdf` 等或 `*` 表示全部；支持 `keyword` 筛选；无匹配时 `other_file_types` 列出章节树里的其他资源类型） |
| `download_courseware` | 将全部或指定 `keyword` 匹配的课件（默认 PPT/PPTX，`file_types` 同上）下载到容器 `downloads/courseware/` |
| `fetch_homework` | 抓取指定课程（或 `homework_query` 匹配）的作业，保存 JSON/Markdown 并**直接返回 Markdown 正文** |
| `list_downloads` | 浏览服务端 `downloads/` 中已保存的文件列表 |
| `read_download` | 直接读取服务端 `downloads/` 下的 Markdown/JSON 文本内容 |
| `get_status_monitor` | 读取后台定时扫描器的最近快照与变更事件 |
| `trigger_status_scan` | 立即触发一次状态扫描 |

另外提供标准 MCP Resources：`eduplus://courses`、`eduplus://tasks`、`eduplus://downloads/{relative_path}`。

### 不同 Agent / 客户端如何添加 MCP

下面以通过 Tunnel 暴露的通用地址 `https://your-tunnel.example.com/mcp` 为例（若在内网直连则替换为 `http://<TVBOX_IP>:8000/mcp`，并设置 `EDUPLUS_BIND=0.0.0.0`、把该 IP 加进 `EDUPLUS_MCP_ALLOWED_HOSTS`）。除非关闭了鉴权，请求头都要带 `Authorization: Bearer <token>`：

**1. 通用 JSON 配置（适用于多数支持远程 HTTP MCP 的桌面客户端）**

```json
{
  "mcpServers": {
    "eduplus": {
      "url": "https://your-tunnel.example.com/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_MCP_TOKEN"
      }
    }
  }
}
```

如果客户端需要显式指定传输类型（如 VS Code / Cursor 等）：

```json
{
  "mcpServers": {
    "eduplus": {
      "type": "http",
      "url": "https://your-tunnel.example.com/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_MCP_TOKEN"
      }
    }
  }
}
```

**2. Claude Code CLI**

```bash
claude mcp add --transport http eduplus https://your-tunnel.example.com/mcp \
  --header "Authorization: Bearer YOUR_MCP_TOKEN"
```

**3. OpenCode（`opencode.json`）**

```json
{
  "mcp": {
    "eduplus": {
      "type": "remote",
      "url": "https://your-tunnel.example.com/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_MCP_TOKEN"
      }
    }
  }
}
```

**4. Codex CLI（`~/.codex/config.toml`）**

```toml
[mcp_servers.eduplus]
url = "https://your-tunnel.example.com/mcp"
bearer_token_env_var = "EDUPLUS_MCP_TOKEN"   # 在运行 Codex 的环境中导出该变量
```

## 作业与实验状态检测 API

网页服务同时提供只读状态接口（规格见 [`docs/openapi/status-api.yaml`](docs/openapi/status-api.yaml)），设置 `EDUPLUS_STATUS_TOKEN` 后开启：

```bash
EDUPLUS_STATUS_TOKEN="自己生成的长随机字符串" python3 -m eduplus_tools.web --host 127.0.0.1 --port 8000

curl -H "Authorization: Bearer $EDUPLUS_STATUS_TOKEN" \
  "http://127.0.0.1:8000/api/v1/tasks?state=not_submitted"
```

相关环境变量：

```text
EDUPLUS_STATUS_TOKEN=                         # 留空则不对外开启 /api/v1，但后台扫描与 MCP 仍可用
EDUPLUS_STATUS_SCAN_INTERVAL_SECONDS=600      # 扫描间隔，最少 60 秒
EDUPLUS_STATUS_DB=downloads/status/status.sqlite3
EDUPLUS_STATUS_CONFIG=config.json             # 读取 SESSION 的配置文件
```

## Telegram 作业提醒

Telegram bot 是状态检测 API 的消费方，单独运行，只通过 HTTP 读取 `/api/v1`：

```bash
EDUPLUS_STATUS_TOKEN="和网页服务相同的 token" \
TELEGRAM_BOT_TOKEN="BotFather 给的 token" \
TELEGRAM_CHAT_ID="你的 chat id" \
python3 -m eduplus_tools.notify --status-url http://127.0.0.1:8000
```

Docker 部署时用 `docker compose --profile telegram up -d --build` 同时启动网页/MCP 服务和 bot。

## 命令行用法

### 查询登录态、课程与任务状态

```bash
# 校验当前登录态
python3 -m eduplus_tools session --json

# 列出未结课的课程及其 ID（--all-courses 包含已结课）
python3 -m eduplus_tools courses --json

# 查看未结课课程里的全部作业和实验
python3 -m eduplus_tools tasks --state not_submitted --due-within 24 --json
python3 -m eduplus_tools tasks --overdue --json
python3 -m eduplus_tools tasks --all-courses --course-id "课程 ID" --type experiment --json
```

### 查找、下载课件与抓取作业

```bash
# 预览 PPT/PPTX 列表与签名链接（支持 --keyword 与 --json）
python3 -m eduplus_tools ppt --course-id "课程 ID" --keyword "第3章" --dry-run --json

# 下载全部或指定关键词的课件
python3 -m eduplus_tools ppt --course-id "课程 ID" --keyword "第3章"

# 课件是 PDF 等其他格式时，用 --file-types 指定（"*" 表示全部附件）
python3 -m eduplus_tools ppt --course-id "课程 ID" --file-types pdf,pptx --dry-run

# 抓取作业并转换 Markdown（支持 --homework 按名称或 ID 过滤，支持 --json）
python3 -m eduplus_tools homework --course-id "课程 ID" --homework "第一次作业" --homework-status-mode undone --homework-answer-mode both --json

# 同时下载课件与抓取作业
python3 -m eduplus_tools all --course-id "课程 ID"
```

## 给 Agent 的本地技能

[`skills/eduplus-status/SKILL.md`](skills/eduplus-status/SKILL.md) 提供面向本地 CLI 环境的完整操作流程（课程、任务、课件检索下载、作业获取与作答分析、配置与服务管理），不设人为权限限制；若部署在远程容器，推荐直接使用上面的 **HTTP MCP 服务**。

## 致谢

作业抓取与导出部分参考并基于 [RealYasuHaru/EDUPLUS-Homework-Scraper](https://github.com/RealYasuHaru/EDUPLUS-Homework-Scraper) 的实现思路重做，感谢原项目对 EDUPLUS 作业接口和文本导出流程的整理。
