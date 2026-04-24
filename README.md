# EDUPLUS Tools

基于原 PPT 下载器和作业抓取脚本重做的新版 U+ / EDUPLUS 工具箱，用同一份登录配置处理同一门课的课件和作业。

## 文件分类

```text
eduplus_tools/
  cli.py        # 统一命令行入口
  config.py     # config.json 与命令行配置解析
  client.py     # U+ API 请求与 Cookie/Header
  ppt.py        # PPT/PPTX 课件下载
  homework.py   # 作业抓取、JSON 保存、文本转换

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

## 注意

- `config.json`、`downloads/` 已加入 `.gitignore`。
- 作业接口返回答案字段时，工具会同时生成“不带答案”和“带答案”两个文本版本。
- `--dry-run` 只适用于 PPT 下载预览；运行 `all --dry-run` 时会跳过作业抓取。

## 致谢

作业抓取与导出部分参考并基于 [RealYasuHaru/EDUPLUS-Homework-Scraper](https://github.com/RealYasuHaru/EDUPLUS-Homework-Scraper) 的实现思路重做，感谢原项目对 EDUPLUS 作业接口和文本导出流程的整理。
