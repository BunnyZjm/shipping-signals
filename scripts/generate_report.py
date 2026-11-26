#!/usr/bin/env python3
"""从周报生成月度形势分析报告。

默认使用确定性汇总（事实只来自周报条目）；--polish 可调用 AI 润色表述，
但提示词禁止新增事实或数字，润色失败时自动回退到确定性版本。
"""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import date
from pathlib import Path

import lib

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "inclusionai/ling-3.2-flash:free"

BOARD_OWNERS = {
    "container": "商务 / 航线",
    "chemical": "化工团队",
    "contract": "合同物流",
    "engineering": "工程物流",
    "air": "空运 / 销售",
    "inspection": "操作 / 合规",
    "tally": "客服 / 操作",
}

SIGNAL_RE = re.compile(
    r"(?<![\d.])(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s*"
    r"(%|个百分点|点|美元/FEU|美元/TEU|美元/柜|美元|万TEU|万标箱|TEU|标箱|家|条|架次|平方米|㎡|艘|亿元|万美元)"
)
NEGATIVE = ("跌", "下降", "下滑", "减少", "回落", "负增长", "承压", "亏损")
POSITIVE = ("涨", "增长", "上升", "提升", "刷新", "突破", "增加", "改善")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成航策月度形势分析")
    parser.add_argument("--month", help="YYYY-MM，默认上一个月")
    parser.add_argument("--status", choices=("final", "month-to-date"), default="final")
    parser.add_argument("--out", help="输出路径，默认 data/reports/MONTH.json")
    parser.add_argument("--polish", action="store_true", help="调用 AI 润色表述（不新增事实）")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def previous_month(reference: date) -> str:
    year, month = reference.year, reference.month - 1
    if month == 0:
        year, month = year - 1, 12
    return f"{year}-{month:02d}"


def month_title(month: str) -> str:
    year, value = month.split("-")
    return f"{year} 年 {int(value)} 月航运物流形势分析"


def load_month_editions(month: str) -> list[dict]:
    editions = []
    for path in sorted(lib.EDITIONS_DIR.glob(f"{month}-*.json")):
        try:
            edition = lib.read_json(path)
        except Exception:
            continue
        if not edition.get("date", "").startswith(month):
            continue
        editions.append(edition)
    editions.sort(key=lambda item: item["date"])
    return editions


def first_sentence(text: str, limit: int = 120) -> str:
    text = " ".join((text or "").split())
    match = re.match(r"^(.+?[。！？!?])", text)
    sentence = match.group(1) if match else text
    return lib.truncate(sentence, limit)


def dedupe_text(values: list[str], limit: int, length: int = 100) -> list[str]:
    result: list[str] = []
    for value in values:
        text = lib.truncate(str(value).strip(), length)
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def collect_entries(editions: list[dict]) -> tuple[list[dict], list[dict]]:
    items: list[dict] = []
    briefs: list[dict] = []
    seen_urls: set[str] = set()
    for edition in editions:
        for item in edition.get("items", []):
            url = lib.normalize_url(item.get("sourceUrl", ""))
            if url in seen_urls:
                continue
            seen_urls.add(url)
            enriched = dict(item)
            enriched["_issue"] = edition.get("issue")
            enriched["_editionDate"] = edition.get("date")
            items.append(enriched)
        for brief in edition.get("briefs", []):
            url = lib.normalize_url(brief.get("sourceUrl", ""))
            if url in seen_urls:
                continue
            seen_urls.add(url)
            enriched = dict(brief)
            enriched["_issue"] = edition.get("issue")
            enriched["_editionDate"] = edition.get("date")
            briefs.append(enriched)
    return items, briefs


def build_related(entries: list[dict], limit: int = 6) -> list[dict]:
    related = []
    for entry in entries[:limit]:
        related.append({
            "title": entry.get("title", ""),
            "source": entry.get("source", ""),
            "date": entry.get("publishedAt", ""),
            "url": entry.get("sourceUrl", ""),
        })
    return related


def build_board_sections(items: list[dict], briefs: list[dict], boards: dict[str, dict]) -> list[dict]:
    by_board: dict[str, dict] = {}
    for item in items:
        by_board.setdefault(item["board"], {"items": [], "briefs": []})["items"].append(item)
    for brief in briefs:
        by_board.setdefault(brief["board"], {"items": [], "briefs": []})["briefs"].append(brief)

    order = {board_id: index for index, board_id in enumerate(boards)}
    sections = []
    for board_id in sorted(by_board, key=lambda value: order.get(value, 99)):
        group = by_board[board_id]
        board_items = sorted(
            group["items"],
            key=lambda entry: (entry.get("importance") == "high", entry.get("publishedAt", "")),
            reverse=True,
        )
        board_briefs = sorted(group["briefs"], key=lambda entry: entry.get("publishedAt", ""), reverse=True)
        primary = board_items[0] if board_items else board_briefs[0]
        if not primary:
            continue
        summaries = [entry.get("summary", "") for entry in board_items[:2]]
        if not summaries and board_briefs:
            summaries = [board_briefs[0].get("summary", "")]
        summary = lib.truncate(" ".join(summaries), 520)
        trends = dedupe_text([entry.get("whyMatters", "") for entry in board_items[:3]], 4, 110)
        implication_pool: list[str] = []
        for entry in board_items[:3]:
            implication_pool.extend(entry.get("implications", []))
        implications = dedupe_text(implication_pool, 3, 100)
        watch_pool: list[str] = []
        for entry in board_items[:3]:
            watch_pool.extend(entry.get("actions", []))
        watch = dedupe_text(watch_pool, 3, 80)
        if not watch and board_briefs:
            watch = [lib.truncate(board_briefs[0].get("summary", ""), 80)]
        related = build_related(board_items + board_briefs)
        sections.append({
            "board": board_id,
            "headline": primary.get("title", ""),
            "summary": summary,
            "trends": trends or [first_sentence(summary, 100)],
            "implications": implications or [first_sentence(board_items[0].get("whyMatters", ""), 100)] if board_items else [first_sentence(primary.get("summary", ""), 100)],
            "watch": watch or [first_sentence(primary.get("summary", ""), 80)],
            "related": related,
        })
    return sections


def extract_signals(items: list[dict], boards: dict[str, dict], month: str, edition_count: int, board_count: int) -> list[dict]:
    candidates = sorted(
        items,
        key=lambda entry: (entry.get("importance") == "high", entry.get("publishedAt", "")),
        reverse=True,
    )
    signals: list[dict] = []
    used_labels: set[str] = set()
    for item in candidates:
        if len(signals) >= 4:
            break
        summary = item.get("summary", "")
        match = SIGNAL_RE.search(summary)
        if not match:
            continue
        value, unit = match.group(1), match.group(2)
        try:
            numeric = float(value.replace(",", ""))
        except ValueError:
            continue
        if 2020 <= numeric <= 2035 and unit not in {"家", "条", "艘", "架次"}:
            continue
        board_name = boards.get(item["board"], {}).get("short", item.get("board", ""))
        tag = (item.get("tags") or [""])[0]
        label = f"{board_name} · {tag}" if tag else board_name
        if label in used_labels:
            continue
        used_labels.add(label)
        start = max(0, match.start() - 40)
        window = summary[start: match.end() + 40]
        if any(word in window for word in NEGATIVE):
            tone = "down"
        elif any(word in window for word in POSITIVE):
            tone = "up"
        else:
            tone = "neutral"
        signals.append({
            "label": lib.truncate(label, 16),
            "value": value,
            "unit": unit,
            "note": lib.truncate(item.get("title", ""), 42),
            "tone": tone,
        })
    fillers = [
        {"label": "覆盖周报", "value": str(edition_count), "unit": "期", "note": f"{month} 月已收录", "tone": "neutral"},
        {"label": "收录要闻", "value": str(len(items)), "unit": "条", "note": "均附原文链接", "tone": "neutral"},
        {"label": "覆盖板块", "value": str(board_count), "unit": "个", "note": "按货代业务划分", "tone": "neutral"},
    ]
    for filler in fillers:
        if len(signals) >= 3:
            break
        if filler["label"] not in used_labels:
            signals.append(filler)
            used_labels.add(filler["label"])
    return signals[:5]


def build_report(month: str, status: str, editions: list[dict], items: list[dict], briefs: list[dict], boards: dict[str, dict]) -> dict:
    sections = build_board_sections(items, briefs, boards)
    board_count = len(sections)
    issues = [edition.get("issue") for edition in editions if edition.get("issue")]
    issue_label = f"第 {min(issues)}–{max(issues)} 周" if issues else "本月"
    title = month_title(month)
    subtitle = f"定稿版 · 基于{issue_label}周报" if status == "final" else f"月中版 · 基于{issue_label}周报"

    top_items = sorted(items, key=lambda entry: (entry.get("importance") == "high", entry.get("publishedAt", "")), reverse=True)
    highlight = "；".join(
        f"{boards.get(item['board'], {}).get('short', item.get('board'))}："
        + re.sub(r"[。！？!?]+$", "", first_sentence(item.get("whyMatters", ""), 70))
        for item in top_items[:3]
    )
    executive = (
        f"{month.replace('-', ' 年 ')} 月，航策共收录 {len(editions)} 期周报、{len(items)} 条要闻，"
        f"覆盖 {board_count} 个业务板块。{highlight}。报告按业务板块汇总事实、走势与货代可执行动作，"
        f"所有数字均来自周报所引原文，判断部分为编辑分析。"
    )
    judgements = [
        f"{boards.get(item['board'], {}).get('name', item.get('board'))}：{first_sentence(item.get('whyMatters', ''), 80)}"
        for item in top_items[:3]
    ]
    if len(judgements) < 2:
        judgements.append(f"本月共覆盖 {board_count} 个板块，信息密度集中在线索明确的少数方向。")
        judgements.append("对暂未覆盖的板块，应结合客户订单与口岸动态单独判断，不用周报替代一线信息。")

    actions = []
    seen_boards: set[str] = set()
    for item in top_items:
        if item["board"] in seen_boards:
            continue
        seen_boards.add(item["board"])
        action = (item.get("actions") or [""])[0]
        if not action:
            continue
        actions.append({
            "horizon": "下月" if status == "final" else "本月",
            "action": lib.truncate(action, 110),
            "owner": BOARD_OWNERS.get(item["board"], "业务团队"),
        })
        if len(actions) >= 6:
            break

    report = {
        "id": month,
        "month": month,
        "title": title,
        "subtitle": subtitle,
        "status": status,
        "generatedAt": lib.iso_date(lib.today_bj()),
        "coverNote": (
            "本报告由周报自动汇总生成；所有条目附原文链接，业务判断为编辑分析。"
            if status == "final"
            else "本报告为月中滚动版，月末将依据完整月度的周报重新生成定稿版。"
        ),
        "executiveSummary": executive,
        "keyJudgements": judgements,
        "signals": extract_signals(items, boards, month, len(editions), board_count),
        "boards": sections,
        "actions": actions,
        "method": (
            f"本报告基于 {month} 月 {len(editions)} 期周报（{issue_label}）的公开新闻整理，"
            f"共 {len(items)} 条要闻、{len(briefs)} 条简报，覆盖 {board_count} 个业务板块。"
            "所有条目均附原文链接；业务判断为编辑分析，不构成商业决策依据。"
        ),
    }
    return report


def polish_report(report: dict, model: str | None, base_url: str) -> dict:
    api_key = (os.environ.get("AI_API_KEY") or "").strip()
    if not api_key:
        print("未配置 AI_API_KEY，跳过润色，使用确定性版本。", flush=True)
        return report
    import compose  # 复用同一个兼容 OpenAI 的调用器与模型链

    board_payload = {
        section["board"]: {
            "headline": section["headline"],
            "summary": section["summary"],
            "trends": section["trends"],
            "implications": section["implications"],
            "watch": section["watch"],
        }
        for section in report["boards"]
    }
    prompt = f"""下面是 {report['month']} 月航运物流形势分析报告的确定性草稿。请只改进表达：
- 允许合并、压缩、调整语序，使文字更像研究报告；
- 严禁新增草稿中没有的事实、公司名、数字、日期或因果判断；
- 所有数字必须原样保留，不得换算单位；
- 不要输出 Markdown，只输出 JSON。

需要改写的字段：
{{
  "executiveSummary": "200–320 字",
  "keyJudgements": ["判断 1", "判断 2", "判断 3"],
  "boards": {{
    "板块 id": {{
      "summary": "120–220 字，仅基于草稿事实",
      "trends": ["走势 1", "走势 2", "走势 3"],
      "implications": ["影响 1", "影响 2", "影响 3"]
    }}
  }}
}}

草稿：
{json.dumps({
    'month': report['month'],
    'executiveSummary': report['executiveSummary'],
    'keyJudgements': report['keyJudgements'],
    'signals': report['signals'],
    'boards': board_payload,
    'actions': report['actions'],
}, ensure_ascii=False, indent=1)}
"""
    messages = [
        {"role": "system", "content": "你是航运物流研究编辑，只做文字润色，不创造新事实。"},
        {"role": "user", "content": prompt},
    ]
    try:
        composer = compose.Composer(compose.model_chain(model), api_key, base_url)
        content, used_model = composer.chat(messages, max_tokens=4000)
        polished = compose.parse_json_content(content)
        if not isinstance(polished, dict):
            raise ValueError("模型返回的不是 JSON 对象")
    except Exception as exc:
        print(f"润色失败，使用确定性版本：{exc}", flush=True)
        return report

    if isinstance(polished.get("executiveSummary"), str) and len(polished["executiveSummary"]) >= 120:
        report["executiveSummary"] = polished["executiveSummary"][:600]
    if isinstance(polished.get("keyJudgements"), list) and len(polished["keyJudgements"]) >= 2:
        report["keyJudgements"] = dedupe_text(polished["keyJudgements"], 4, 140)
    polished_boards = polished.get("boards")
    if isinstance(polished_boards, dict):
        for section in report["boards"]:
            update = polished_boards.get(section["board"])
            if not isinstance(update, dict):
                continue
            if isinstance(update.get("summary"), str) and len(update["summary"]) >= 40:
                section["summary"] = update["summary"][:600]
            for key in ("trends", "implications"):
                values = update.get(key)
                if isinstance(values, list) and len(values) >= 2:
                    section[key] = dedupe_text(values, 4, 110)
    report["generatedBy"] = {"mode": "ai-polish", "model": used_model, "generatedAt": lib.now_iso()}
    return report


def main() -> int:
    args = parse_args()
    month = args.month or previous_month(lib.today_bj())
    if not re.match(r"^\d{4}-(0[1-9]|1[0-2])$", month):
        raise SystemExit("--month 需要 YYYY-MM 格式")
    editions = load_month_editions(month)
    if not editions:
        print(f"{month} 没有找到周报，先发布周报再生成月报。", flush=True)
        return 3
    items, briefs = collect_entries(editions)
    if not items:
        print(f"{month} 的周报中没有要闻条目。", flush=True)
        return 3
    boards = lib.load_boards()
    report = build_report(month, args.status, editions, items, briefs, boards)
    if args.polish:
        report = polish_report(report, os.environ.get("AI_MODEL"), os.environ.get("AI_BASE_URL") or DEFAULT_BASE_URL)
    errors = lib.validate_report(report, boards)
    if errors:
        print("月报校验失败，未写入：", flush=True)
        for error in errors:
            print(f"  - {error}", flush=True)
        return 2
    out_path = args.out or str(lib.REPORTS_DIR / f"{month}.json")
    if args.dry_run:
        print(json.dumps(report, ensure_ascii=False, indent=1)[:2000])
        print(f"\ndry-run：未写入 {out_path}")
        return 0
    lib.write_json(Path(out_path), report)
    lib.log(f"月报已写入 {out_path}：{len(report['boards'])} 个板块，{len(report['signals'])} 个信号")
    print("提示：运行 python3 scripts/build_index.py 更新索引。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
