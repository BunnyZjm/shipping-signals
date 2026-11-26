#!/usr/bin/env python3
"""把候选新闻整理成一周要闻。

优先使用已抓取原文的候选；所有事实必须来自候选材料。生成结果先通过
validate_edition 校验，通过后才写入 data/editions/。失败时保留草稿，
不会把不完整内容发布到站点。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from datetime import date
from pathlib import Path

import lib

DEFAULT_BASE_URL = "https://models.github.ai/inference"
DEFAULT_MODEL = "openai/gpt-4o-mini"

SYSTEM_PROMPT = """你是「航策」的航运物流情报编辑，服务对象是中国货代与航运物流公司的经营团队。
你的任务：从候选材料中为每个业务板块挑选 3–5 条要闻，汇编成可以直接发布的中文周报。

硬性规则：
1. 只能使用候选材料里明确出现的事实、数字、公司名、地点和日期。不得补充材料外的事实，不得编造数字，不得把推测写成事实。
2. 每条要闻的原文链接由系统根据候选材料自动绑定，你只需要输出 candidateId，不要改写链接或来源。
3. 优先选择 verified=true（已抓取原文）的材料；7 个业务板块各选 3–5 条，每个板块最多 5 条，总条数 21–35 条。某个板块本周确实没有足够的重大新闻时，可以少于 3 条，但不要用不相关新闻凑数。
4. 每条 summary 100–200 字，只陈述事实；whyMatters 60–140 字，面向货代经营；implications 2–4 条，偏战略判断；actions 2–4 条，具体可执行；tags 2–4 个。
5. 标题保持与原文事实一致，克制、专业，不使用“震惊”“暴涨”“彻底”等标题党措辞。
6. 板块材料不足时宁可少选，也不要编造、重复或拿不相关的新闻凑数。
7. 只输出 JSON，不要 Markdown 代码块，不要解释。"""

OUTPUT_SCHEMA = """{
  "title": "本周主题，12–22 个汉字",
  "intro": "80–140 字，概括本周几条主线与共同关键词",
  "items": [
    {
      "candidateId": "候选材料的 id",
      "title": "新闻标题",
      "summary": "100–200 字，只写事实",
      "whyMatters": "60–140 字，为什么与货代有关",
      "implications": ["发展启示 1", "发展启示 2", "发展启示 3"],
      "actions": ["本周可执行动作 1", "动作 2", "动作 3"],
      "tags": ["标签1", "标签2", "标签3"],
      "importance": "high | medium | low"
    }
  ],
  "briefs": [
    {
      "candidateId": "候选材料的 id",
      "title": "简报标题",
      "summary": "60–120 字，只写事实"
    }
  ]
}"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="用 AI 将候选新闻整理为周报")
    parser.add_argument("--candidates", default=str(lib.AUTOMATION_DIR / "candidates.json"))
    parser.add_argument("--date", help="周报日期 YYYY-MM-DD，默认今天（北京时间）")
    parser.add_argument("--out", help="输出到 data/editions/YYYY-MM-DD.json")
    parser.add_argument("--draft-out", help="草稿输出路径，默认 automation/drafts/edition-DATE.json")
    parser.add_argument("--base-url", default=os.environ.get("AI_BASE_URL") or DEFAULT_BASE_URL)
    parser.add_argument("--model", default=os.environ.get("AI_MODEL") or DEFAULT_MODEL)
    parser.add_argument("--max-candidates", type=int, default=35)
    parser.add_argument("--from-json", help="跳过 API，读取已保存的模型/人工 JSON（便于离线测试）")
    parser.add_argument("--dry-run", action="store_true", help="只生成草稿，不写入 data/editions")
    return parser.parse_args()


def candidate_payload(candidates: list[dict], max_items: int) -> list[dict]:
    boards_order = list(lib.load_config().get("boards", {}).keys())
    by_board: dict[str, list[dict]] = {}
    for candidate in candidates:
        if candidate.get("board") not in boards_order:
            continue
        if not candidate.get("publishedAt"):
            continue
        material = len(candidate.get("excerpt") or "") + len(candidate.get("summary") or "")
        if material < 120:
            continue
        by_board.setdefault(candidate["board"], []).append(candidate)

    payload: list[dict] = []
    for board in boards_order:
        entries = by_board.get(board, [])
        entries.sort(
            key=lambda item: (
                bool(item.get("verified")),
                item.get("publishedAt", ""),
                len(item.get("excerpt") or ""),
                item.get("score", 0),
            ),
            reverse=True,
        )
        for candidate in entries[:5]:
            payload.append({
                "id": candidate["id"],
                "board": board,
                "boardName": candidate.get("boardName", board),
                "title": candidate["title"],
                "source": candidate.get("source", ""),
                "publishedAt": candidate.get("publishedAt", ""),
                "verified": bool(candidate.get("verified")),
                "summary": lib.truncate(candidate.get("summary", ""), 500),
                "excerpt": lib.truncate(candidate.get("excerpt", ""), 1400),
            })
    return payload[:max_items]


def call_model(payload: dict, api_key: str, base_url: str, retry_without_format: bool = True) -> dict:
    url = base_url.rstrip("/") + "/chat/completions"
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handle = tempfile.NamedTemporaryFile(prefix="signals-ai-", suffix=".json", delete=False)
    payload_path = handle.name
    handle.write(body)
    handle.close()
    response_path = payload_path + ".out"
    command = [
        "curl", "-sS", "--max-time", "240", "-X", "POST", url,
        "-H", f"Authorization: Bearer {api_key}",
        "-H", "Content-Type: application/json",
        "-H", "Accept: application/json",
        "--data-binary", f"@{payload_path}",
        "-o", response_path,
        "-w", "%{http_code}",
    ]
    try:
        import subprocess
        result = subprocess.run(command, capture_output=True, text=True, timeout=260)
        status_text = result.stdout.strip()
        status = int(status_text) if status_text.isdigit() else 0
        raw = Path(response_path).read_text(encoding="utf-8", errors="replace") if Path(response_path).exists() else ""
        lib.log(f"AI 响应：HTTP {status}，{len(raw)} 字节")
        if status == 400 and retry_without_format and "response_format" in payload:
            payload.pop("response_format", None)
            lib.log("模型不支持 response_format，重试一次（从文本中解析 JSON）")
            return call_model(payload, api_key, base_url, retry_without_format=False)
        if not 200 <= status < 300:
            raise RuntimeError(f"AI 接口返回 HTTP {status}：{raw[:500]}")
        if not raw.strip():
            raise RuntimeError(f"AI 接口返回 HTTP {status} 但响应体为空")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"AI 响应不是 JSON（HTTP {status}）：{raw[:300]}") from exc
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"AI 响应结构异常（HTTP {status}）：{raw[:300]}") from exc
        usage = data.get("usage", {})
        if usage:
            lib.log(f"token 用量：{usage}")
        if not (content or "").strip():
            raise RuntimeError(f"AI 返回空内容；原始响应：{raw[:300]}")
        try:
            return parse_json_content(content)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"AI 返回内容无法解析为 JSON：{content[:300]}") from exc
    finally:
        Path(payload_path).unlink(missing_ok=True)
        Path(response_path).unlink(missing_ok=True)


def parse_json_content(content: str) -> dict:
    text = (content or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.S)
        if match:
            return json.loads(match.group(0))
        raise


def request_edition(payload: dict, api_key: str, base_url: str) -> dict:
    """调用模型；空响应或解析失败时，关闭 response_format 再试一次。"""
    first = json.loads(json.dumps(payload, ensure_ascii=False))
    relaxed = json.loads(json.dumps(payload, ensure_ascii=False))
    relaxed.pop("response_format", None)
    relaxed["max_tokens"] = min(int(relaxed.get("max_tokens", 4096)), 4096)
    last_error: Exception | None = None
    for index, attempt in enumerate((first, relaxed), 1):
        try:
            if index > 1:
                lib.log("第一次未拿到有效内容，关闭 response_format 后重试")
            return call_model(attempt, api_key, base_url)
        except Exception as exc:  # 网络波动、空响应、内容截断都值得再试一次
            last_error = exc
            lib.log(f"第 {index} 次调用失败：{exc}")
    raise RuntimeError(str(last_error))


def clean_list(values, limit: int, item_limit: int = 120) -> list[str]:
    result = []
    for value in values or []:
        text = lib.truncate(str(value).strip(), item_limit)
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def build_edition(result: dict, candidates: list[dict], edition_date: date, model: str) -> dict:
    by_id = {candidate["id"]: candidate for candidate in candidates}
    boards = lib.load_boards()
    board_order = {board_id: index for index, board_id in enumerate(boards)}
    limits = lib.load_config().get("settings", {})
    max_per_board = int(limits.get("weeklyMaxPerBoard", 5))
    max_total = int(limits.get("weeklyMaxItems", 35))
    issue = lib.iso_week(edition_date)
    items = []
    used_candidates: set[str] = set()
    board_counts: dict[str, int] = {}
    for raw in result.get("items") or []:
        candidate = by_id.get(str(raw.get("candidateId", "")))
        if not candidate:
            raise ValueError(f"模型选择了不存在的 candidateId：{raw.get('candidateId')}")
        if candidate["id"] in used_candidates:
            continue
        board_id = candidate["board"]
        if board_counts.get(board_id, 0) >= max_per_board:
            continue
        summary = lib.truncate(str(raw.get("summary", "")).strip(), 320)
        why = lib.truncate(str(raw.get("whyMatters", "")).strip(), 260)
        implications = clean_list(raw.get("implications"), 4, 110)
        actions = clean_list(raw.get("actions"), 4, 110)
        tags = clean_list(raw.get("tags"), 4, 12)
        if not summary or not why or len(implications) < 2 or len(actions) < 2 or len(tags) < 2:
            continue
        importance = str(raw.get("importance", "medium")).lower()
        if importance not in {"high", "medium", "low"}:
            importance = "medium"
        number = board_counts.get(board_id, 0) + 1
        board_counts[board_id] = number
        items.append({
            "id": f"c{issue}-{board_id}-{number}",
            "board": board_id,
            "title": lib.clean_title(str(raw.get("title", "")).strip()) or candidate["title"],
            "source": candidate["source"],
            "sourceUrl": candidate["url"],
            "publishedAt": candidate["publishedAt"],
            "summary": summary,
            "whyMatters": why,
            "implications": implications,
            "actions": actions,
            "tags": tags[:4],
            "importance": importance,
            "verified": bool(candidate.get("verified")),
        })
        used_candidates.add(candidate["id"])
        if len(items) >= max_total:
            break

    items.sort(key=lambda item: (board_order.get(item["board"], 99), item["id"]))

    briefs = []
    used_brief_boards: set[str] = set()
    for raw in result.get("briefs") or []:
        candidate = by_id.get(str(raw.get("candidateId", "")))
        if not candidate or candidate["id"] in used_candidates:
            continue
        if candidate["board"] in used_brief_boards:
            continue
        summary = lib.truncate(str(raw.get("summary", "")).strip(), 180)
        if len(summary) < 60:
            fallback = lib.truncate(str(candidate.get("summary", "")).strip(), 180)
            summary = fallback if len(fallback) >= 60 else ""

        if not summary:
            continue
        used_brief_boards.add(candidate["board"])
        briefs.append({
            "id": f"b{issue}-{candidate['board']}",
            "board": candidate["board"],
            "title": lib.clean_title(str(raw.get("title", "")).strip()) or candidate["title"],
            "source": candidate["source"],
            "sourceUrl": candidate["url"],
            "publishedAt": candidate["publishedAt"],
            "summary": summary,
        })
        used_candidates.add(candidate["id"])
        if len(briefs) >= 3:
            break

    return {
        "id": lib.iso_date(edition_date),
        "issue": issue,
        "date": lib.iso_date(edition_date),
        "publishedAt": f"{lib.iso_date(edition_date)}T08:00:00+08:00",
        "window": lib.week_window(edition_date),
        "title": lib.truncate(str(result.get("title", "")).strip(), 40) or "本周航运物流信号",
        "intro": lib.truncate(str(result.get("intro", "")).strip(), 220),
        "items": items,
        "briefs": briefs,
        "generatedBy": {
            "mode": "ai",
            "model": model,
            "generatedAt": lib.now_iso(),
            "source": "automation/compose.py",
        },
    }


def main() -> int:
    args = parse_args()
    edition_date = lib.parse_date(args.date) if args.date else lib.today_bj()
    if not edition_date:
        raise SystemExit("--date 需要 YYYY-MM-DD 格式")
    candidates_path = Path(args.candidates)
    if not candidates_path.exists():
        raise SystemExit(f"候选文件不存在：{candidates_path}，请先运行 collect.py")
    candidates_doc = lib.read_json(candidates_path)
    payload_candidates = candidate_payload(candidates_doc.get("candidates", []), args.max_candidates)
    if len({item["board"] for item in payload_candidates}) < 3 or len(payload_candidates) < 15:
        print("材料不足：至少需要 3 个板块、15 条可用候选，未生成周报。", flush=True)
        print("建议检查 automation/candidates.json、扩大回看窗口，或在 automation/inbox.json 手动补充链接。", flush=True)
        return 3

    api_key = os.environ.get("AI_API_KEY") or os.environ.get("GITHUB_TOKEN") or ""
    if not api_key and not args.from_json:
        print("未配置 AI_API_KEY 或 GITHUB_TOKEN。可先在本地人工编辑，或配置任意 OpenAI 兼容接口后重试。", flush=True)
        return 4

    out_path = Path(args.out) if args.out else lib.EDITIONS_DIR / f"{lib.iso_date(edition_date)}.json"
    draft_path = Path(args.draft_out) if args.draft_out else lib.DRAFTS_DIR / f"edition-{lib.iso_date(edition_date)}.json"

    user_prompt = f"""周报日期：{lib.iso_date(edition_date)}
收录窗口：{lib.week_window(edition_date)['from']} 至 {lib.week_window(edition_date)['to']}

业务板块：集装箱物流、化工物流、合同物流、工程物流、航空物流、检验检测、理货

候选材料（JSON 数组）：
{json.dumps(payload_candidates, ensure_ascii=False, indent=1)}

请为每个板块挑选 3–5 条要闻（单板块最多 5 条，总数 21–35 条；素材不足的板块可以少选），可附 0–3 条简报。严格按以下结构输出：
{OUTPUT_SCHEMA}

补充要求：
- 如果候选材料中的数字、单位或因果关系没有写清楚，就在文案里回避该细节，不要猜。
- 如果某条材料 verified=false（只拿到 RSS 摘要、未抓取原文），只能使用 summary 字段中的信息，不要扩写。
- briefs 只用于值得记录但不进入本周主线的信息，可以是未入选板块。"""

    request_payload = {
        "model": args.model,
        "temperature": 0.2,
        "max_tokens": 9000,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    }
    try:
        if args.from_json:
            lib.log(f"读取离线生成结果 {args.from_json}")
            result = lib.read_json(Path(args.from_json))
        else:
            lib.log(f"调用模型 {args.model}，候选 {len(payload_candidates)} 条")
            result = request_edition(request_payload, api_key, args.base_url)
    except Exception as exc:
        message = f"AI 生成失败：{exc}"
        print(message, flush=True)
        summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary_path:
            try:
                Path(summary_path).write_text(f"### AI 生成失败\n\n```\n{message}\n```\n", encoding="utf-8")
            except OSError:
                pass
        return 4

    try:
        edition = build_edition(result, candidates_doc.get("candidates", []), edition_date, args.model)
    except (ValueError, TypeError) as exc:
        print(f"生成结果结构异常：{exc}", flush=True)
        return 2

    lib.write_json(draft_path, edition)
    errors = lib.validate_edition(edition, lib.load_boards(), strict=True)
    if errors:
        print("草稿未通过校验，已保留在 automation/drafts/，不会发布：", flush=True)
        for error in errors:
            print(f"  - {error}", flush=True)
        return 2
    if args.dry_run:
        lib.log(f"dry-run：草稿已写入 {draft_path}")
        return 0
    lib.write_json(out_path, edition)
    lib.log(f"已写入 {out_path}（{len(edition['items'])} 条要闻，{len(edition.get('briefs', []))} 条简报）")
    print(f"请检查后运行：python3 scripts/build_index.py && python3 scripts/validate_data.py --strict", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
