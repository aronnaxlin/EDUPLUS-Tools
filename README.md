# EDUPLUS 工具

用于下载 EDUPLUS 课件和抓取作业内容的工具，支持命令行和网页界面两种使用方式。

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
  web/
    server.py   # 网页界面服务入口
    static/     # HTML/CSS/JS 静态资源

config.json          # 本地真实配置，不要提交
config.example.json  # 配置模板
downloads/
  courseware/        # PPT/PPTX 输出
  homework/
    json/            # 作业原始 JSON
    text/
      不带答案/
      带答案/
```

## 配置

复制配置模板，并填写自己的 `SESSION` 和课程 ID：

```bash
cp config.example.json config.json
```

`config.json` 已加入 `.gitignore`，不要提交自己的登录信息。如果 `SESSION` 失效，重新从浏览器 Cookie 中复制后更新即可。

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

临时覆盖配置：

```bash
python3 -m eduplus_tools all \
  --session "新的SESSION" \
  --course-id "新的课程 ID"
```

## 配置优先级

```text
命令行参数 > --config-json > config.json > 默认值
```

`config.json` 默认从当前工作目录读取；如果当前目录没有，也会回退尝试仓库根目录和包目录，避免目录结构调整后找不到配置文件。

## 说明

- `config.json`、`downloads/` 已加入 `.gitignore`。
- 作业接口返回答案字段时，工具会同时生成“不带答案”和“带答案”两个文本版本。
- `--dry-run` 只适用于 PPT 下载预览；运行 `all --dry-run` 时会跳过作业抓取。
- 网页界面默认按次提交 `SESSION`，不会在服务端落盘；公开部署时更安全，但仍建议加反向代理和访问控制。
- 网页界面的 `公共模式` 会隔离每次任务输出，并要求用户自己填写 `SESSION`；`本地输出` 会直接写入你填写的目录，也可以在受信任环境中复用服务端 `config.json` 里的 `SESSION`。
- 公共模式下，ZIP 下载完成后会立即删除服务器上的任务文件；未下载的任务文件也会按 TTL 自动清理，减少被恶意刷盘的风险。

## 致谢

作业抓取与导出部分参考并基于 [RealYasuHaru/EDUPLUS-Homework-Scraper](https://github.com/RealYasuHaru/EDUPLUS-Homework-Scraper) 的实现思路重做，感谢原项目对 EDUPLUS 作业接口和文本导出流程的整理。
