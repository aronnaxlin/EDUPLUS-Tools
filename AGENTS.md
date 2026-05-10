# Repository Guidelines

## Project Structure & Module Organization

- `eduplus_tools/cli/main.py` is the unified command-line entry point.
- `eduplus_tools/core/` contains configuration loading and the EDUPLUS HTTP client.
- `eduplus_tools/features/` contains task implementations: `ppt.py` for courseware and `homework.py` for homework export.
- `eduplus_tools/web/` contains the web server, job handling, and `static/` HTML/CSS/JS assets.
- `config.example.json` documents local configuration. Real `config.json` and generated `downloads/` output stay uncommitted.
- `Dockerfile`, `docker-compose.yml`, and `start_webui.sh` support web UI deployment.

## Build, Test, and Development Commands

- `python3 -m eduplus_tools --help` shows CLI commands and options.
- `python3 -m eduplus_tools ppt --dry-run` lists courseware without downloading; use this as a smoke test when credentials are configured.
- `python3 -m eduplus_tools homework` fetches homework and converts output files.
- `python3 -m eduplus_tools.web --host 127.0.0.1 --port 8000` runs the web UI locally.
- `bash start_webui.sh` creates `.venv`, installs requirements, and starts the web UI.
- `docker compose up -d --build` builds and runs the containerized web service.

The CLI currently uses only the Python standard library.

## Coding Style & Naming Conventions

Use Python 3 with four-space indentation, `from __future__ import annotations`, dataclasses for config/state, and `pathlib.Path` for filesystem paths. Keep modules focused by layer: shared configuration and HTTP behavior belong in `core`, orchestration in `cli` or `web`, and scraping/export logic in `features`.

Prefer `snake_case` for functions, variables, CLI option destinations, and JSON-derived internal names. Keep browser-facing static code in `eduplus_tools/web/static/` and avoid mixing generated files with source.

## Testing Guidelines

There is no committed automated test suite yet. For risky logic changes, add focused `pytest` tests under `tests/`, with names like `test_config.py` and `test_load_config_prefers_cli_values`. Until then, verify with smoke commands such as `ppt --dry-run`, `/api/health`, and a temporary output directory.

## Commit & Pull Request Guidelines

Recent history uses concise imperative commits, sometimes with Conventional Commit prefixes, for example `feat: refresh web ui and deployment flow` or `dockerize web service with safer public defaults`. Keep the subject short, user-visible, and use `feat:`, `fix:`, or `docs:` when helpful.

Pull requests should include a summary, verification steps, configuration or deployment impact, and screenshots for web UI changes. Never include real `SESSION` values, cookies, generated downloads, or local `config.json`.

## Security & Configuration Tips

Treat EDUPLUS session tokens as secrets. Use `config.example.json` for documentation, pass temporary values with CLI flags when practical, and keep public deployments in the default public-output mode unless the server is private and trusted.
