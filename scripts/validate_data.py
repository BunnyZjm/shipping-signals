#!/usr/bin/env python3
"""校验站点数据。CI 在提交前运行；本地改完 JSON 也应运行一次。"""

from __future__ import annotations

import argparse
from pathlib import Path

import lib


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="校验航策数据文件")
    parser.add_argument("--strict", action="store_true", help="按每板块 3–5 条（总数 21–35 条）、至少 6 个板块校验")
    parser.add_argument("--check-links", action="store_true", help="逐个请求原文链接（较慢）")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    errors: list[str] = []
    warnings: list[str] = []
    index = lib.read_json(lib.INDEX_PATH)
    boards = {board["id"]: board for board in index.get("boards", [])}
    if len(boards) != 7:
        errors.append(f"index.boards 应有 7 个板块，当前 {len(boards)} 个")

    edition_files = sorted(path for path in lib.EDITIONS_DIR.glob("*.json") if not path.name.startswith(("draft", "_")))
    indexed_editions = {item.get("id"): item for item in index.get("editions", [])}
    edition_count = 0
    item_count = 0
    url_pool: list[tuple[str, str]] = []

    for path in edition_files:
        try:
            edition = lib.read_json(path)
        except Exception as exc:
            errors.append(f"{path.name} 无法解析：{exc}")
            continue
        edition_count += 1
        item_count += len(edition.get("items", []))
        errors.extend(lib.validate_edition(edition, boards, strict=args.strict))
        expected_file = f"data/editions/{lib.iso_date(lib.parse_date(edition.get('date')) or lib.today_bj())}.json"
        if expected_file != f"data/editions/{path.name}":
            warnings.append(f"{path.name} 与 date 字段不一致，建议使用 {Path(expected_file).name}")
        meta = indexed_editions.get(edition.get("id"))
        if not meta:
            errors.append(f"{path.name} 未出现在 data/index.json，请运行 build_index.py")
        else:
            if meta.get("itemCount") != len(edition.get("items", [])):
                errors.append(f"{path.name} itemCount 与索引不一致")
            if meta.get("briefCount") != len(edition.get("briefs", [])):
                errors.append(f"{path.name} briefCount 与索引不一致")
        for entry in edition.get("items", []) + edition.get("briefs", []):
            url_pool.append((entry.get("id", "?"), entry.get("sourceUrl", "")))

    for edition_id in indexed_editions:
        if not (lib.EDITIONS_DIR / f"{edition_id}.json").exists():
            errors.append(f"index 引用了不存在的周报：{edition_id}")

    report_files = sorted(lib.REPORTS_DIR.glob("*.json"))
    indexed_reports = {item.get("id"): item for item in index.get("reports", [])}
    for path in report_files:
        try:
            report = lib.read_json(path)
        except Exception as exc:
            errors.append(f"{path.name} 无法解析：{exc}")
            continue
        errors.extend(lib.validate_report(report, boards))
        if report.get("month") and path.stem != report["month"]:
            warnings.append(f"{path.name} 与 month 字段不一致")
        if report.get("id") not in indexed_reports:
            errors.append(f"{path.name} 未出现在 data/index.json，请运行 build_index.py")
        for section in report.get("boards", []):
            for related in section.get("related", []):
                url_pool.append((f"{report.get('id')}/{section.get('board')}", related.get("url", "")))

    if args.check_links:
        seen = set()
        for label, url in url_pool:
            normalized = lib.normalize_url(url) if url else ""
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            result = lib.fetch(url, timeout=15)
            if result.status in {404, 410}:
                errors.append(f"[{label}] 原文链接失效（HTTP {result.status}）：{url}")
            elif not result.ok:
                warnings.append(f"[{label}] 链接暂时不可访问（HTTP {result.status}）：{url}")
            else:
                print(f"  ok {result.status} {url[:100]}")
            lib.sleep(0.3)

    for warning in warnings:
        print(f"WARN  {warning}")
    for error in errors:
        print(f"ERROR {error}")
    print(f"\n检查完成：{edition_count} 期周报 / {item_count} 条要闻 / {len(report_files)} 份月报")
    if errors:
        print(f"发现 {len(errors)} 个错误。")
        return 1
    print("全部通过。" + (f"（{len(warnings)} 条提示）" if warnings else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
