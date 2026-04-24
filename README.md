# EDUPLUS Tools

基于原 PPT 下载器和作业抓取脚本重做的新版 U+ / EDUPLUS 工具箱，用同一份登录配置处理同一门课的课件和作业。

## 文件分类

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
    server.py   # Web UI 服务入口
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

复制配置模板并填写自己的 `SESSION` 和课程 ID：

```bash
cp config.example.json config.json
```

`config.json` 已加入 `.gitignore`，不要提交自己的登录信息。如果 Session 过期，重新从浏览器 Cookie 里复制 `SESSION` 后更新配置即可。

## 运行

Web UI：

```bash
python3 -m eduplus_tools.web --host 0.0.0.0 --port 8000
```

然后访问 `http://服务器地址:8000`。

Docker：

```bash
docker compose up -d --build
```

如果你的环境还是旧版 Compose，也可以用：

```bash
docker-compose up -d --build
```

默认配置是安全偏公共服务的：

- `Local output` 默认关闭
- 公共模式 ZIP 下载完成后立即清理服务端文件
- 未下载的公共任务结果也会按 TTL 自动清理

小白一键启动：

```bash
bash start_webui.sh
```

这个脚本会自动：

- 创建 `.venv` 虚拟环境
- 升级 `pip`
- 安装 `requirements.txt`
- 启动 Web UI

也可以自定义端口：

```bash
PORT=9000 bash start_webui.sh
```

## Web UI 模式

- `Public service`：适合公共服务部署。每个任务都会写入独立目录，例如 `downloads/web-jobs/<job-id>/`，然后提供 ZIP 整包下载。默认下载完成后立即删除服务端文件。
- `Local output`：适合自己部署自己用。任务会直接写入你填写的输出目录，例如 `downloads/`，更接近本地脚本行为。默认关闭，需要显式开启。

两种模式都会保留浏览器里的结果查看和 ZIP 下载，但默认导向不同：

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

如果你确定这台服务器只有自己使用，才建议开启本地模式：

```bash
docker compose run -e EDUPLUS_ENABLE_LOCAL_OUTPUT=true eduplus-web
```

或者直接修改 `compose.yaml`。

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
  --course-id "新的Course ID"
```

## 配置优先级

```text
命令行参数 > --config-json > config.json > 默认值
```

`config.json` 仍然默认从当前工作目录读取；如果当前目录没有，也会回退尝试仓库根目录和包目录，避免内部文件夹重组后路径识别出错。

## 注意

- `config.json`、`downloads/` 已加入 `.gitignore`。
- 作业接口返回答案字段时，工具会同时生成“不带答案”和“带答案”两个文本版本。
- `--dry-run` 只适用于 PPT 下载预览；运行 `all --dry-run` 时会跳过作业抓取。
- Web UI 默认按次提交 SESSION，不在服务端落盘；公开部署时更安全，但仍建议加反向代理和访问控制。
- Web UI 的 `Public service` 模式会隔离每次任务输出；`Local output` 模式则直接写入你填写的目录。
- 公共模式下，ZIP 下载完成后会立即删除服务器上的任务文件；未下载的任务文件也会按 TTL 自动清理，减少被恶意刷盘的风险。

## 致谢

作业抓取与导出部分参考并基于 [RealYasuHaru/EDUPLUS-Homework-Scraper](https://github.com/RealYasuHaru/EDUPLUS-Homework-Scraper) 的实现思路重做，感谢原项目对 EDUPLUS 作业接口和文本导出流程的整理。
