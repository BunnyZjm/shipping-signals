#!/usr/bin/env python3
"""采集候选新闻。

流程：RSS / 列表页 → 关键词分类 → 去重排序 → 抓取原文补齐摘要与日期。
只产出候选文件，不直接发布；发布由 compose.py + validate_data.py 把关。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from datetime import date, timedelta
from difflib import SequenceMatcher
from pathlib import Path

import lib


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="采集航运物流候选新闻")
    parser.add_argument("--date", help="周报日期 YYYY-MM-DD，默认今天（北京时间）")
    parser.add_argument("--lookback", type=int, help="回看天数，默认取配置")
    parser.add_argument("--max-per-board", type=int, help="每个板块保留的候选数")
    parser.add_argument("--articles-per-board", type=int, help="每个板块抓取原文的候选数")
    parser.add_argument("--out", default=str(lib.AUTOMATION_DIR / "candidates.json"))
    parser.add_argument("--inbox", default=str(lib.AUTOMATION_DIR / "inbox.json"), help="手动收录链接的 JSON 文件")
    parser.add_argument("--no-articles", action="store_true", help="只抓列表，不抓原文")
    return parser.parse_args()


def candidate_id(url: str) -> str:
    return "c" + hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]


def clean_excerpt(value: str, limit: int = 2600) -> str:
    text = (value or "").replace("\r", "\n")
    lines = []
    seen = set()
    for line in text.split("\n"):
        line = " ".join(line.split())
        if len(line) < 20 or lib.looks_boilerplate(line):
            continue
        if any(line.lower().startswith(marker.lower()) for marker in lib.RELATED_MARKERS):
            break
        if re.search(r"(\.\.\.|…)\s*20\d{2}[-/.]\d{1,2}[-/.]\d{1,2}\s+\d{1,2}:\d{2}", line):
            break
        key = line[:80]
        if key in seen:
            continue
        seen.add(key)
        lines.append(line)
    return "\n".join(lines)[:limit]


def collect_source(source: dict, kind: str, config: dict, edition_date: date) -> tuple[list[dict], list[str]]:
    warnings: list[str] = []
    candidates: list[dict] = []
    result = lib.fetch(source["url"])
    if not result.ok:
        warnings.append(f"{source['name']} 抓取失败：HTTP {result.status} {result.error}".strip())
        return candidates, warnings
    raw = result.body
    if kind == "feed":
        entries = lib.parse_feed(raw)
        if not entries:
            warnings.append(f"{source['name']} 未解析到 RSS/Atom 条目")
    else:
        entries = lib.extract_links(lib.decode_bytes(raw), result.final_url or source["url"])
        if not entries:
            warnings.append(f"{source['name']} 未抽取到有效链接（可能改版或被反爬拦截）")
    seen: set[str] = set()
    for entry in entries:
        url = entry.get("url", "")
        if not url or url in seen:
            continue
        seen.add(url)
        title = entry.get("title", "")
        summary = entry.get("summary", "")
        required = [str(word).lower() for word in source.get("requireKeywords") or []]
        if required and not any(word in f"{title} {summary}".lower() for word in required):
            continue
        published = lib.parse_date(entry.get("publishedAt")) or lib.extract_url_date(url)
        if not published:
            published = lib.parse_text_date(title, edition_date)
        board, score, hits = lib.classify(title, summary, config, source)
        if not board or score < int(config["settings"].get("minScore", 3)):
            continue
        candidates.append({
            "id": candidate_id(url),
            "board": board,
            "title": title,
            "source": source["name"],
            "url": url,
            "publishedAt": lib.iso_date(published) if published else "",
            "summary": lib.truncate(summary, 500),
            "excerpt": "",
            "verified": False,
            "httpStatus": 0,
            "score": score,
            "matchedKeywords": hits[:4],
            "sourceType": kind,
        })
    return candidates, warnings


def load_inbox(path: Path, config: dict, cutoff: date) -> tuple[list[dict], list[str]]:
    """手动收录：用于反爬、付费墙或需人工判断的来源。

    inbox.json 格式：
    {"items": [{"title": "...", "url": "...", "board": "tally",
                "publishedAt": "2026-11-20", "summary": "可选", "source": "可选",
                "force": false}]}
    """
    warnings: list[str] = []
    if not path.exists():
        return [], warnings
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return [], [f"收件箱读取失败：{exc}"]
    boards = set(config.get("boards", {}))
    candidates: list[dict] = []
    for index, entry in enumerate(data.get("items", []), start=1):
        title = " ".join(str(entry.get("title", "")).split())
        url = str(entry.get("url", "")).strip()
        board = str(entry.get("board", "")).strip()
        if not title or not url or board not in boards:
            warnings.append(f"收件箱第 {index} 条缺少 title/url/有效 board，已跳过")
            continue
        published = lib.parse_date(entry.get("publishedAt"))
        if not published:
            published = lib.extract_url_date(url) or lib.parse_text_date(title)
        if published and published < cutoff and not entry.get("force"):
            warnings.append(f"收件箱第 {index} 条早于回看窗口（{published}），如确需保留请设置 force=true")
            continue
        candidates.append({
            "id": candidate_id(lib.normalize_url(url)),
            "board": board,
            "title": title,
            "source": entry.get("source") or "编辑收件箱",
            "url": lib.normalize_url(url),
            "publishedAt": lib.iso_date(published) if published else "",
            "summary": lib.truncate(entry.get("summary", ""), 500),
            "excerpt": "",
            "verified": False,
            "httpStatus": 0,
            "score": 200,
            "matchedKeywords": ["手动收录"],
            "sourceType": "inbox",
        })
    return candidates, warnings


def dedupe(candidates: list[dict]) -> list[dict]:
    def normalized(title: str) -> str:
        return re.sub(r"[\s\W_]+", "", title.lower())

    accepted: list[dict] = []
    for candidate in sorted(candidates, key=lambda item: item["score"], reverse=True):
        duplicate = None
        for existing in accepted:
            if existing["url"] == candidate["url"]:
                duplicate = existing
                break
            if existing["board"] == candidate["board"]:
                ratio = SequenceMatcher(None, normalized(existing["title"]), normalized(candidate["title"])).ratio()
                if ratio >= 0.88:
                    duplicate = existing
                    break
        if duplicate is None:
            accepted.append(candidate)
        else:
            # 保留信息更完整的一条
            if len(candidate.get("summary", "")) > len(duplicate.get("summary", "")):
                duplicate["summary"] = candidate["summary"]
            duplicate["score"] = max(duplicate["score"], candidate["score"])
    return accepted


def enrich(candidate: dict, edition_date: date) -> None:
    result = lib.fetch(candidate["url"], timeout=25)
    candidate["httpStatus"] = result.status
    if not result.ok:
        candidate["fetchError"] = result.error or f"HTTP {result.status}"
        return
    article = lib.article_extract(lib.decode_bytes(result.body), result.final_url or candidate["url"])
    # 列表页/URL 中的日期通常就是发布时间；正文里的日期可能只是数据日期，不覆盖已有值
    if article.get("publishedAt") and not candidate.get("publishedAt"):
        candidate["publishedAt"] = article["publishedAt"]
    text = article.get("text", "")
    description = article.get("description", "")
    excerpt = clean_excerpt((description + "\n" if description else "") + text)
    if excerpt:
        candidate["excerpt"] = excerpt
    if not candidate.get("summary") and description:
        candidate["summary"] = lib.truncate(description, 500)
    if not candidate.get("summary") and text:
        candidate["summary"] = lib.truncate(text.split("\n")[0], 500)
    candidate["verified"] = len(text) >= 200 and bool(candidate.get("publishedAt"))
    candidate["finalUrl"] = result.final_url or candidate["url"]
    cache_path = lib.ARTICLES_DIR / f"{candidate['id']}.json"
    lib.write_json(cache_path, {
        "id": candidate["id"],
        "url": candidate["url"],
        "finalUrl": candidate.get("finalUrl", ""),
        "title": candidate["title"],
        "articleTitle": article.get("title", ""),
        "publishedAt": candidate.get("publishedAt", ""),
        "httpStatus": result.status,
        "fetchedAt": lib.now_iso(),
        "excerpt": excerpt,
    })


def main() -> int:
    args = parse_args()
    config = lib.load_config()
    settings = config.get("settings", {})
    edition_date = lib.parse_date(args.date) if args.date else lib.today_bj()
    if not edition_date:
        raise SystemExit("--date 需要 YYYY-MM-DD 格式")
    lookback = args.lookback or int(settings.get("lookbackDays", 8))
    max_per_board = args.max_per_board or int(settings.get("maxPerBoard", 8))
    articles_per_board = args.articles_per_board or int(settings.get("articlesPerBoard", 3))
    cutoff = edition_date - timedelta(days=lookback)

    all_candidates: list[dict] = []
    warnings: list[str] = []
    source_count = 0
    for kind, key in (("feed", "feeds"), ("page", "pages")):
        for source in config.get(key, []):
            if not source.get("enabled", True):
                lib.log(f"跳过已禁用来源：{source['name']}")
                continue
            source_count += 1
            lib.log(f"抓取 {source['name']} ({kind})")
            found, source_warnings = collect_source(source, kind, config, edition_date)
            all_candidates.extend(found)
            warnings.extend(source_warnings)
            lib.log(f"  → {len(found)} 条候选")
            time.sleep(0.4)

    deduped = dedupe(all_candidates)
    fresh: list[dict] = []
    stale = 0
    for candidate in deduped:
        published = lib.parse_date(candidate.get("publishedAt"))
        if published and published < cutoff:
            stale += 1
            continue
        fresh.append(candidate)

    inbox, inbox_warnings = load_inbox(Path(args.inbox), config, cutoff)
    warnings.extend(inbox_warnings)
    existing_urls = {item["url"] for item in fresh}
    inbox = [item for item in inbox if item["url"] not in existing_urls]
    fresh = inbox + fresh
    if inbox:
        lib.log(f"收件箱补充 {len(inbox)} 条")

    boards_order = list(config.get("boards", {}).keys())
    selected: list[dict] = []
    per_board: dict[str, list[dict]] = {}
    inbox_ids = {item["id"] for item in inbox}
    for board in boards_order:
        board_items = [item for item in fresh if item["board"] == board]
        board_items.sort(key=lambda item: (item["id"] in inbox_ids, item.get("publishedAt", ""), item["score"]), reverse=True)
        chosen = board_items[: max(max_per_board, articles_per_board)]
        per_board[board] = chosen
        selected.extend(chosen[:max_per_board])

    if not args.no_articles:
        for board in boards_order:
            targets = [
                item for item in per_board.get(board, [])
                if item["id"] in inbox_ids
                or not item.get("publishedAt")
                or lib.parse_date(item["publishedAt"]) >= cutoff
            ][: max(articles_per_board, len(inbox))]
            for candidate in targets:
                lib.log(f"抓取原文 [{board}] {lib.truncate(candidate['title'], 34)}")
                try:
                    enrich(candidate, edition_date)
                except Exception as exc:  # 网络问题不应中断整批采集
                    candidate["fetchError"] = str(exc)[:200]
                time.sleep(0.6)

    # 抓取原文后再次剔除过旧内容，并补全日期
    final: list[dict] = []
    final_ids: set[str] = set()
    for candidate in inbox + selected:
        if candidate["id"] in final_ids:
            continue
        final_ids.add(candidate["id"])
        published = lib.parse_date(candidate.get("publishedAt"))
        if published and published < cutoff and candidate["id"] not in inbox_ids:
            continue
        if not candidate.get("publishedAt"):
            lib.log(f"  跳过无日期候选：{lib.truncate(candidate['title'], 30)}")
            continue
        final.append(candidate)

    boards_index = {board["id"]: board for board in lib.read_json(lib.INDEX_PATH).get("boards", [])}
    final.sort(key=lambda item: (boards_order.index(item["board"]), not item.get("verified"), -item["score"]))
    for candidate in final:
        candidate["boardName"] = boards_index.get(candidate["board"], {}).get("name", candidate["board"])
        candidate["title"] = lib.clean_title(candidate["title"])

    verified_count = sum(1 for item in final if item.get("verified"))
    output = {
        "generatedAt": lib.now_iso(),
        "editionDate": lib.iso_date(edition_date),
        "lookbackDays": lookback,
        "window": lib.week_window(edition_date),
        "stats": {
            "sources": source_count,
            "rawCandidates": len(all_candidates),
            "afterDedupe": len(deduped),
            "staleSkipped": stale,
            "published": len(final),
            "verified": verified_count,
            "boardsCovered": len({item["board"] for item in final}),
        },
        "warnings": warnings,
        "candidates": final,
    }
    out_path = Path(args.out)
    lib.write_json(out_path, output)

    lib.log("采集完成")
    print(json.dumps(output["stats"], ensure_ascii=False, indent=2))
    for candidate in final:
        marker = "✓" if candidate.get("verified") else "·"
        print(f"  {marker} {candidate['board']:<11} {candidate['publishedAt'] or '????-??-??'}  {lib.truncate(candidate['title'], 60)}")
    if warnings:
        print("\n警告：")
        for warning in warnings:
            print(f"  - {warning}")
    print(f"\n候选文件：{out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
