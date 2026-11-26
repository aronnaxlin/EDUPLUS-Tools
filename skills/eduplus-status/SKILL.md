---
name: eduplus-status
description: >
  通过 EDUPLUS-Tools 查询 EDUPLUS（U+、优加平台）的课程、作业和实验状态、截止时间，
  查找和下载 PPT/PPTX 课件，获取作业题目与答案并导出为本地 JSON/Markdown，
  以及管理本地配置、Web 服务、Docker 与 MCP 服务。
  用户问“还有什么没交”“最近有什么要截止”“找一下某门课的课件”“下载 PPT”
  “把这门课的作业题目拿下来”或“更新配置/启动服务”时使用。
  Also use for EDUPLUS courses, courseware/PPT search and download, homework retrieval,
  experiment submission status and deadlines, and EDUPLUS-Tools deployment/MCP setup.
compatibility: 需要命令执行工具、Python 3（含已安装的依赖）或连接 EDUPLUS-Tools 的 MCP HTTP 服务。
---

# EDUPLUS 全功能操作指南

本技能覆盖 EDUPLUS-Tools 的全部能力：课程查询、作业与实验状态、课件查找与下载（`ppt`）、作业抓取与导出（`homework`）、全量同步（`all`）、登录态校验（`session`）、配置维护以及 Web / Docker / MCP 服务管理。对人与 AI 均开放完整权限，不设人为功能限制。

## 运行位置与凭据

1. 在 EDUPLUS-Tools 仓库根目录运行命令，或优先使用项目虚拟环境 `.venv/bin/python`（若已配置远程 MCP 服务，也可直接调用对应的 MCP 工具）。
2. 凭据优先级为：`命令行参数 > 运行时覆盖 > --config-json > 在线更新并保存的凭据 (data/credentials.json) > 环境变量 (EDUPLUS_*) > config.json > 默认值`。
3. `courses`、`tasks`、`session` 只需要 `session`；`ppt`、`homework`、`all` 还需要 `course_id`（可从 `courses --json` 获取后通过 `--course-id` 传入）。
4. `session` 对应浏览器登录 `www.eduplus.net` 后开发者工具 **Local Storage → AccessToken** 的值。如果用户要求更新 `config.json` 或设置 `EDUPLUS_SESSION`，可直接协助更新配置文件或环境变量；如遇登录失效且用户未提供新 token，再提示用户重新登录后提供或自行更新。

## 常用命令

### 1. 登录态与课程/任务查询

```bash
# 检查当前登录态是否有效
python3 -m eduplus_tools session --json

# 当前未结课的课程及 ID
python3 -m eduplus_tools courses --json

# 全部课程，包括已结课课程
python3 -m eduplus_tools courses --all-courses --json

# 当前未结课课程的全部作业与实验
python3 -m eduplus_tools tasks --json

# 未提交任务（包括逾期与无截止时间）
python3 -m eduplus_tools tasks --state not_submitted --json

# 接下来 24 小时内截止且未提交的任务（不含已逾期）
python3 -m eduplus_tools tasks --state not_submitted --due-within 24 --json

# 已逾期且未提交的任务
python3 -m eduplus_tools tasks --overdue --json

# 指定课程的实验或历史课程任务
python3 -m eduplus_tools tasks --all-courses --course-id "课程 ID" --type experiment --json
```

### 2. 查找与下载 PPT/PPTX 课件

```bash
# 先预览课程课件列表与临时签名下载链接（不落盘），支持关键词过滤
python3 -m eduplus_tools ppt --course-id "课程 ID" --dry-run --json
python3 -m eduplus_tools ppt --course-id "课程 ID" --keyword "第3章" --dry-run --json

# 下载全部或匹配的课件到本地输出目录
python3 -m eduplus_tools ppt --course-id "课程 ID" --output downloads
python3 -m eduplus_tools ppt --course-id "课程 ID" --keyword "进程" --overwrite
```

### 3. 抓取与导出作业（题目与答案）

```bash
# 导出全部作业（默认 plain 不带答案），可用 --json 返回生成的文件路径列表
python3 -m eduplus_tools homework \
  --course-id "课程 ID" \
  --output "输出目录" \
  --homework-answer-mode plain \
  --homework-status-mode all \
  --json

# 按提交状态或作业名称/ID 筛选，并按需导出答案
python3 -m eduplus_tools homework \
  --course-id "课程 ID" \
  --homework "第一次作业" \
  --homework-status-mode undone \
  --homework-answer-mode both \
  --skip-existing-homework-convert \
  --json

# 同时抓取课件与作业
python3 -m eduplus_tools all --course-id "课程 ID" --output downloads
```

- `--homework-status-mode` 可选 `all`、`done`、`undone`（依据 `subStatus` 判断提交状态）。
- `--homework-answer-mode` 可选 `plain`（不带答案）、`answers`（带答案）、`both`（生成两种 Markdown）。
- 导出结果位于 `输出目录/homework/json/{做过,没做过}/` 与 `输出目录/homework/markdown/{做过,没做过}/`。用户要求解释、解答或核对题目时，直接读取导出的 Markdown 并协助分析或作答。

### 4. Web / Docker / MCP 服务

```bash
# 启动一体化 Web UI + 状态 API + HTTP MCP（默认监听 8000，MCP 路径为 /mcp）
python3 -m eduplus_tools.web --host 127.0.0.1 --port 8000

# 或单独启动 MCP 服务（默认监听 8001，支持 streamable-http / sse / stdio）
python3 -m eduplus_tools.mcp --host 127.0.0.1 --port 8001

# Docker Compose 一键部署
mkdir -p downloads data
docker compose up -d --build
```

HTTP MCP 默认需要 `Authorization: Bearer <token>`：token 取自 `EDUPLUS_MCP_TOKEN`，未设置时自动生成并保存在 `data/mcp-token`。通过域名访问时，要把域名加进 `EDUPLUS_MCP_ALLOWED_HOSTS`。

## 状态查询与结果解读

- 保留 `--json` 命令的退出码与标准错误：`0` 为成功（仍需检查 `partial_failures`），`1` 为上游请求或配置错误，`2` 为登录态失效或参数错误。
- `state` 含义：`not_submitted`（未提交）、`submitted`（已提交待批改）、`graded`（已批改）；仅当 `overdue=true` 时表示未提交且已过截止时间。
- 时间均以北京时间（UTC+8）输出；`due_at=null` 表示无截止时间。
- 回答状态查询时，建议以清晰表格展示：**课程｜类型｜任务（含链接）｜状态｜截止时间**，并说明筛选范围及任何 `partial_failures`。
