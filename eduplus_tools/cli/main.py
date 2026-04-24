from __future__ import annotations

import argparse
from pathlib import Path

from ..core.client import EduplusClient
from ..core.config import load_config, mask_value
from ..features.homework import scrape_homework
from ..features.ppt import download_ppt_files


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Unified EDUPLUS courseware and homework tool.")
    parser.add_argument("command", choices=["all", "ppt", "homework"], nargs="?", default="all", help="Task to run. Default: all")
    parser.add_argument("--config", "--config-file", dest="config", default="config.json", help="JSON config file. Default: config.json")
    parser.add_argument("--config-json", help='Optional JSON config, e.g. {"session":"...","course_id":"..."}')
    parser.add_argument("--session", help="U+ SESSION/x-access-token")
    parser.add_argument("--course-id", "--course_id", dest="course_id", help="U+ course ID")
    parser.add_argument("--course-name", "--course_name", dest="course_name", help="Optional course name override")
    parser.add_argument("--hm-lvt", "--hm_lvt", dest="hm_lvt", help="Optional Hm_lvt cookie value")
    parser.add_argument("--base-url", default=None, help="U+ host")
    parser.add_argument("--output", "-o", default=None, help="Output root directory. Default: downloads")
    parser.add_argument("--timeout", type=int, default=None, help="HTTP timeout seconds")
    parser.add_argument("--dry-run", action="store_true", help="For ppt: list files and signed URLs without downloading")
    parser.add_argument("--overwrite", action="store_true", help="For ppt: overwrite existing files")
    parser.add_argument("--skip-existing-homework-convert", action="store_true", help="Only convert homework JSON fetched in this run")
    parser.add_argument("--verbose", action="store_true", help="Print extra request details")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config = load_config(
        config_file=args.config,
        config_json=args.config_json,
        session=args.session,
        course_id=args.course_id,
        hm_lvt=args.hm_lvt,
        course_name=args.course_name,
        base_url=args.base_url,
        output=args.output,
        timeout=args.timeout,
    )
    client = EduplusClient(config, verbose=args.verbose)
    output_root = Path(config.output)

    print("=" * 60)
    print("EDUPLUS Tools")
    print("=" * 60)
    if config.config_path:
        print(f"Config: {config.config_path}")
    print(f"Course ID: {config.course_id}")
    print(f"SESSION: {mask_value(config.session)}")
    print(f"Hm_lvt: {mask_value(config.hm_lvt)}")
    print(f"Output: {output_root}")
    print("=" * 60)

    status = 0
    if args.command in {"all", "ppt"}:
        status = max(
            status,
            download_ppt_files(
                client,
                course_id=config.course_id,
                course_name=config.course_name,
                output_root=output_root,
                dry_run=args.dry_run,
                overwrite=args.overwrite,
            ),
        )

    if args.command in {"all", "homework"} and not args.dry_run:
        status = max(
            status,
            scrape_homework(
                client,
                course_id=config.course_id,
                output_root=output_root,
                convert_existing=not args.skip_existing_homework_convert,
            ),
        )
    elif args.command == "all" and args.dry_run:
        print("Skipping homework because --dry-run only applies to ppt downloads.")

    return status


if __name__ == "__main__":
    raise SystemExit(main())
