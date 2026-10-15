# EDUPLUS 工具

用于下载 EDUPLUS 课件、抓取作业内容，以及查询作业和实验提交状态的工具，支持命令行和网页界面。

## 目录说明

```text
eduplus_tools/
  cli/
    main.py     # 统一命令行入口
  core/
    config.py   # config.json 与命令行配置解析
    client.py   # U+ API 请求与 Cookie/Header
  features/
    ppt.py      # PPT/PPTX 课件下载
    homework.py # 作业抓取、JSON 保存、文本转换
    status.py   # 课程、作业与实验状态抓取及筛选
  monitor/      # 定时扫描、快照差异与 SQLite 存储
  notify/       # Telegram 提醒 bot（通过 HTTP 消费状态 API）
  web/
    server.py   # 网页界面服务入口
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

## 配置

复制配置模板，填写登录凭据 `session`；下载课件或作业时还需要填写 `course_id`：

```bash
cp config.example.json config.json
```

`session` 字段和 `--session` 参数应填入登录后 `www.eduplus.net` 浏览器开发者工具中 **Local Storage → AccessToken** 的值。请求通过 `x-access-token` 头鉴权，只复制 `SESSION` Cookie 不足以访问接口。凭据失效后，在浏览器重新登录，再更新这个值。

`config.json` 已加入 `.gitignore`，不要提交自己的登录信息，也不要把 token 粘贴到日志或截图里。

## 启动方式

网页界面：

```bash
python3 -m eduplus_tools.web --host 0.0.0.0 --port 8000
```

启动后访问 `http://服务器地址:8000`。

Docker：

```bash
docker compose up -d --build
```

如果你的环境还是旧版 Compose，也可以用：

```bash
docker-compose up -d --build
```

默认配置更适合公共部署：

- `本地输出` 默认关闭
- 公共模式下，ZIP 下载完成后会立即清理服务端文件
- 未下载的公共任务结果也会按 TTL 自动清理

常见启动方式：

```bash
# 改端口
PORT=9000 docker compose up -d --build

# 开启本地输出模式
EDUPLUS_ENABLE_LOCAL_OUTPUT=true docker compose up -d --build
```

一键启动：

```bash
bash start_webui.sh
```

脚本会自动执行以下步骤：

- 创建 `.venv` 虚拟环境
- 升级 `pip`
- 安装 `requirements.txt`
- 启动网页界面

也可以自定义端口：

```bash
PORT=9000 bash start_webui.sh
```

也支持直接把最新的网页界面参数带进去：

```bash
# 开启本地输出模式
bash start_webui.sh --enable-local-output

# 调整公共模式目录和清理时间
bash start_webui.sh \
  --public-output-root downloads/web-jobs \
  --bundle-root downloads/web-bundles \
  --public-job-ttl-seconds 3600
```

## 网页界面模式

- `公共模式`：适合公共部署。每个任务都会写入独立目录，例如 `downloads/web-jobs/<job-id>/`，完成后可下载 ZIP。用户需要自己填写 `SESSION`，服务端不会读取默认 `config.json` 里的 `SESSION`；默认在下载完成后立即删除服务端文件。
- `本地输出`：适合自己部署自己使用。任务会直接写入你填写的输出目录，例如 `downloads/`。默认关闭，需要显式开启；如果服务端存在 `config.json`，可留空 `SESSION` 并复用其中的默认值。

两种模式都支持在浏览器中查看结果和下载 ZIP，主要区别是默认输出位置和 `SESSION` 来源不同：

- 公共模式优先任务隔离和下载分发
- 本地模式优先直接落盘到你的目录

## 作业与实验状态检测 API

网页服务可以额外开启一组只读接口，定时扫描所有课程的作业和实验，记录提交状态、截止时间，以及两次扫描之间的变化（新布置、已提交、已批改、撤回、截止时间变更等）。接口规格见 [`docs/openapi/status-api.yaml`](docs/openapi/status-api.yaml)，Telegram 通知等消费方只需要读这组接口。

这组接口用服务端 `config.json` 里的 `SESSION` 访问 EDUPLUS，也可以用环境变量 `EDUPLUS_SESSION` 覆盖。因此只适合自己私有部署，默认关闭，设置访问 token 后才会开启：

```bash
EDUPLUS_STATUS_TOKEN="自己生成的长随机字符串" python3 -m eduplus_tools.web --host 127.0.0.1 --port 8000

curl -H "Authorization: Bearer $EDUPLUS_STATUS_TOKEN" \
  "http://127.0.0.1:8000/api/v1/tasks?state=not_submitted"
```

相关环境变量：

```text
EDUPLUS_STATUS_TOKEN=                         # 留空则不开启
EDUPLUS_STATUS_SCAN_INTERVAL_SECONDS=600      # 扫描间隔，最少 60 秒
EDUPLUS_STATUS_DB=downloads/status/status.sqlite3
EDUPLUS_STATUS_CONFIG=config.json             # 读取 SESSION 的配置文件
EDUPLUS_SESSION=                              # 可选，覆盖配置文件里的 SESSION
```

服务启动后会立即扫描一次，之后按间隔扫描。第一次扫描只记录现状，不会把已有作业当成新布置的。每次扫描都会重新读取配置文件，更新 `SESSION` 后不需要重启。

## Telegram 作业提醒

Telegram bot 是状态检测 API 的一个消费方，单独运行，只通过 HTTP 读取上面那组接口：

- 推送新布置的作业和实验、截止时间变更、批改结果、变回未提交（自己撤回或老师打回）、任务被删除，以及 EDUPLUS 登录态失效和恢复。
- 未提交的任务在截止前 24 小时和 3 小时各提醒一次；截止时间改了会重新提醒。
- 命令：`/todo` 查看未提交和已逾期的任务，`/status` 查看登录态和最近一次扫描，`/scan` 立即扫描。
- 只给 `TELEGRAM_CHAT_ID` 推送，也只响应这个聊天里的命令。其他人发命令会收到自己的 chat id 和拒绝提示，可以借此查到自己的 chat id。

用 [@BotFather](https://t.me/BotFather) 创建 bot 拿到 token，先开启状态检测 API，再启动 bot：

```bash
EDUPLUS_STATUS_TOKEN="和网页服务相同的 token" \
TELEGRAM_BOT_TOKEN="BotFather 给的 token" \
TELEGRAM_CHAT_ID="你的 chat id" \
python3 -m eduplus_tools.notify --status-url http://127.0.0.1:8000
```

相关环境变量：

```text
EDUPLUS_STATUS_URL=http://127.0.0.1:8000
TELEGRAM_REMINDER_HOURS=24,3                  # 截止前多少小时提醒
TELEGRAM_POLL_INTERVAL_SECONDS=60             # 检查新事件和提醒的间隔
TELEGRAM_STATE_FILE=downloads/status/telegram-state.json
```

第一次启动只记下事件流的位置，不补发历史事件；推送失败的事件会在下一轮重发。Docker 部署时用 `docker compose --profile telegram up -d --build` 同时启动网页服务和 bot。

## Docker 配置

常用环境变量：

```text
EDUPLUS_ENABLE_LOCAL_OUTPUT=false
EDUPLUS_AUTO_DELETE_PUBLIC_DOWNLOADS=true
EDUPLUS_PUBLIC_JOB_TTL_SECONDS=1800
EDUPLUS_CLEANUP_INTERVAL_SECONDS=60
EDUPLUS_PUBLIC_OUTPUT_ROOT=downloads/web-jobs
EDUPLUS_BUNDLE_ROOT=downloads/web-bundles
EDUPLUS_LOCAL_OUTPUT_ROOT=downloads
```

如果你确定这台服务器只给自己使用，才建议开启 `本地输出`：

```bash
EDUPLUS_ENABLE_LOCAL_OUTPUT=true docker compose up -d --build
```

也可以直接修改 `docker-compose.yml`，或通过 `.env` 文件覆盖这些变量。

## 命令行用法

### 查询课程、作业与实验状态

这两条命令只需要 `session`，不需要 `course_id`，也不需要启动网页服务。它们直接请求 EDUPLUS，不读取或写入检测服务的快照和事件。

```bash
# 列出未结课的课程及其 ID
python3 -m eduplus_tools courses

# 查看未结课课程里的全部作业和实验，按截止时间排序，无截止时间的排最后
python3 -m eduplus_tools tasks

# 未提交的任务（包括已逾期的任务）
python3 -m eduplus_tools tasks --state not_submitted

# 接下来 24 小时内截止、尚未提交的任务，不含已逾期任务
python3 -m eduplus_tools tasks --state not_submitted --due-within 24

# 只看已逾期且未提交的任务
python3 -m eduplus_tools tasks --overdue

# 只看某门课的实验
python3 -m eduplus_tools tasks --course-id "课程 ID" --type experiment

# 查询已结课课程；courses 也支持 --all-courses
python3 -m eduplus_tools tasks --all-courses --course-id "已结课的课程 ID"

# 输出 JSON，适合脚本或 agent 读取
python3 -m eduplus_tools courses --json
python3 -m eduplus_tools tasks --state submitted --state graded --json
```

`tasks` 的筛选参数：

| 参数 | 含义 |
|---|---|
| `--state` | `not_submitted`（未提交）、`submitted`（已提交待批改）、`graded`（已批改），可重复 |
| `--type` | `homework`（作业）、`experiment`（实验），可重复 |
| `--course-id` | 只看指定课程；配置文件里的 `course_id` 不会限制状态查询 |
| `--due-within HOURS` | 截止时间严格晚于当前时间、早于 HOURS 小时后；HOURS 必须是正的有限数，可带小数 |
| `--overdue` | 只看截止时间已过且未提交的任务 |
| `--all-courses` | 包含已结课课程；默认只看未结课课程 |

同一参数的多个值取并集，不同筛选条件取交集。时间以北京时间（UTC+8）显示；没有截止时间的任务不匹配 `--due-within` 或 `--overdue`。

`--json` 的标准输出只包含 JSON，警告和 `--verbose` 请求日志写入标准错误。`courses` 返回 `items`；`tasks` 返回 `fetched_at`、`items` 和 `partial_failures`，任务字段与 `/api/v1/tasks` 一致，但直接查询不记录首次发现时间，`first_seen_at` 为空字符串。

退出码：成功为 `0`，上游请求整体失败为 `1`，登录态失效或命令行参数错误为 `2`。个别课程抓取失败时仍返回 `0`，成功抓到的任务照常输出；请检查 `partial_failures` 或标准错误里的警告，不要将部分结果视为完整列表。

### 下载课件与作业

下载课件并抓取作业：

```bash
python3 -m eduplus_tools all
```

只下载 PPT/PPTX：

```bash
python3 -m eduplus_tools ppt
```

先预览 PPT/PPTX 列表，不下载：

```bash
python3 -m eduplus_tools ppt --dry-run
```

只抓取作业并转换文本：

```bash
python3 -m eduplus_tools homework
```

默认导出不带答案的 JSON 和 Markdown。需要答案时可指定：

```bash
# 只导出带答案版本
python3 -m eduplus_tools homework --homework-answer-mode answers

# 同时导出不带答案和带答案 Markdown，JSON 保留答案字段
python3 -m eduplus_tools homework --homework-answer-mode both
```

只抓取某个作答范围：

```bash
# 只抓取未做的作业
python3 -m eduplus_tools homework --homework-status-mode undone

# 只抓取做过的作业
python3 -m eduplus_tools homework --homework-status-mode done
```

临时覆盖配置：

```bash
python3 -m eduplus_tools all \
  --session "新的SESSION" \
  --course-id "新的课程 ID"
```

## 给通用 agent 的技能

[`skills/eduplus-status/SKILL.md`](skills/eduplus-status/SKILL.md) 提供课程、作业与实验状态查询，以及作业题目获取和 JSON/Markdown 导出流程。默认导出不带答案；用户明确需要时可导出平台已提供的答案。技能不代为提交、撤回或修改平台数据，也不涉及部署、通知配置或自动续期。

将 `skills/eduplus-status/` 作为技能目录交给支持 `SKILL.md` 的 agent 加载，并告知本仓库的实际路径。运行时使用本地已配置的 `config.json`，不要把凭据复制进技能文件。可用“查 U+ 还有什么没交”或“把这门课的未交作业题目导出成 Markdown”测试。

## 配置优先级

```text
命令行参数 > --config-json > config.json > 默认值
```

`config.json` 默认从当前工作目录读取；如果当前目录没有，也会回退尝试仓库根目录和包目录，避免目录结构调整后找不到配置文件。

## 说明

- `config.json`、`downloads/` 已加入 `.gitignore`。
- 作业会按 `做过`、`没做过` 分目录输出；默认两者都抓取且不导出答案字段。
- `--dry-run` 只适用于 PPT 下载预览；运行 `all --dry-run` 时会跳过作业抓取。
- 网页界面默认按次提交 `SESSION`，不会在服务端落盘；公开部署时更安全，但仍建议加反向代理和访问控制。
- 网页界面的 `公共模式` 会隔离每次任务输出，并要求用户自己填写 `SESSION`；`本地输出` 会直接写入你填写的目录，也可以在受信任环境中复用服务端 `config.json` 里的 `SESSION`。
- 公共模式下，ZIP 下载完成后会立即删除服务器上的任务文件；未下载的任务文件也会按 TTL 自动清理，减少被恶意刷盘的风险。

## 致谢

作业抓取与导出部分参考并基于 [RealYasuHaru/EDUPLUS-Homework-Scraper](https://github.com/RealYasuHaru/EDUPLUS-Homework-Scraper) 的实现思路重做，感谢原项目对 EDUPLUS 作业接口和文本导出流程的整理。
