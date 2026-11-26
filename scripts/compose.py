#!/usr/bin/env python3
"""把候选新闻整理成一周要闻。

做法：按业务板块分批调用模型（每次只给它 3–5 条候选、只要求 3–5 条成稿），
生成结果先过 validate_edition，通过后才写入 data/editions/。

这样做的原因：
- 单次让模型写 21–35 条容易超时、截断或被限流；分板块后每次输出都很小；
- 某个板块出问题只影响该板块，其他板块照常出稿；
- 主模型不可用时自动切换到备用模型，避免整期失败。

所有事实必须来自候选材料，原文链接由系统按 candidateId 绑定。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tempfile
import time
from collections import Counter
from datetime import date
from pathlib import Path

import lib

DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
# OpenRouter 免费模型（2026-11 校对）；可用 Variables 的 AI_MODEL 覆盖，
# 多个模型用英文逗号分隔，按顺序降级。
DEFAULT_MODELS = (
    "inclusionai/ling-3.2-flash:free",
    "baidu/ernie-5.5-flash-preview:free",
    "google/gemma-4-31b-it:free",
    "google/gemma-4-26b-a4b-it:free",
    "openrouter/free",
)
MAX_CALLS = 18

SYSTEM_PROMPT = """你是「航策」的航运物流情报编辑，服务对象是中国货代与航运物流公司的经营团队。
你会拿到一个业务板块的候选材料，请从中挑选要闻并写成可直接发布的中文文案。

硬性规则：
1. 只能使用候选材料里明确出现的事实、数字、公司名、地点和日期。不得补充材料外的事实，不得猜测数字，不得把推测写成事实。
2. 每条要闻必须填写候选材料里的 id 作为 candidateId，不要改写链接或来源。
3. 优先选择 verified=true（已抓取原文）的材料；材料不够就少选，不要拿不相关的新闻凑数，同一条材料只能用一次。
4. summary 100–200 字，只陈述事实；whyMatters 60–140 字，说明对货代经营的影响；implications 2–4 条战略判断；actions 2–4 条本周可执行动作；tags 2–4 个短语。
5. 标题克制、专业，与原文事实一致，不使用“震惊”“暴涨”“彻底”等标题党措辞。
6. 只输出 JSON 对象，不要 Markdown 代码块，不要解释。"""

ITEM_SCHEMA = """{
  "items": [
    {
      "candidateId": "候选材料的 id",
      "title": "新闻标题",
      "summary": "100–200 字，只写事实",
      "whyMatters": "60–140 字，为什么与货代有关",
      "implications": ["发展启示 1", "发展启示 2"],
      "actions": ["本周可执行动作 1", "动作 2"],
      "tags": ["标签1", "标签2"],
      "importance": "high | medium | low"
    }
  ]
}"""


class ApiError(RuntimeError):
    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.status = status


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="用 AI 将候选新闻整理为周报")
    parser.add_argument("--candidates", default=str(lib.AUTOMATION_DIR / "candidates.json"))
    parser.add_argument("--date", help="周报日期 YYYY-MM-DD，默认今天（北京时间）")
    parser.add_argument("--out", help="输出到 data/editions/YYYY-MM-DD.json")
    parser.add_argument("--draft-out", help="草稿输出路径，默认 automation/drafts/edition-DATE.json")
    parser.add_argument("--base-url", default=os.environ.get("AI_BASE_URL") or DEFAULT_BASE_URL)
    parser.add_argument("--model", help="模型 id，多个用英文逗号分隔；默认使用内置免费模型链")
    parser.add_argument("--max-candidates", type=int, default=5, help="每个板块最多送多少条候选")
    parser.add_argument("--from-json", help="跳过 API，读取已保存的模型/人工 JSON（便于离线测试）")
    parser.add_argument("--dry-run", action="store_true", help="只生成草稿，不写入 data/editions")
    return parser.parse_args()


def model_chain(raw: str | None) -> list[str]:
    if not raw:
        return list(DEFAULT_MODELS)
    models = [item.strip() for item in raw.split(",") if item.strip()]
    return models or list(DEFAULT_MODELS)


# ---------------------------------------------------------------- 候选材料

def group_candidates(candidates: list[dict], max_items: int) -> dict[str, list[dict]]:
    """按板块分组，每个板块取最新的若干条，转成精简后的送模型结构。"""
    boards_order = list(lib.load_config().get("boards", {}).keys())
    grouped: dict[str, list[dict]] = {}
    for candidate in candidates:
        board = candidate.get("board")
        if board not in boards_order:
            continue
        if not candidate.get("publishedAt"):
            continue
        if len(candidate.get("excerpt") or "") + len(candidate.get("summary") or "") < 120:
            continue
        grouped.setdefault(board, []).append(candidate)

    payload: dict[str, list[dict]] = {}
    for board in boards_order:
        entries = grouped.get(board, [])
        entries.sort(
            key=lambda item: (
                bool(item.get("verified")),
                item.get("publishedAt", ""),
                len(item.get("excerpt") or ""),
                item.get("score", 0),
            ),
            reverse=True,
        )
        picked = []
        seen_urls: set[str] = set()
        for candidate in entries:
            url = candidate.get("url", "")
            if url and url in seen_urls:
                continue
            seen_urls.add(url)
            picked.append({
                "id": candidate["id"],
                "board": board,
                "title": candidate["title"],
                "source": candidate.get("source", ""),
                "url": candidate.get("url", ""),
                "publishedAt": candidate.get("publishedAt", ""),
                "verified": bool(candidate.get("verified")),
                "summary": lib.truncate(candidate.get("summary", ""), 400),
                "excerpt": lib.truncate(candidate.get("excerpt", ""), 1100),
            })
            if len(picked) >= max_items:
                break
        if picked:
            payload[board] = picked
    return payload


# ---------------------------------------------------------------- 模型调用

def call_model(
    messages: list[dict],
    api_key: str,
    base_url: str,
    model: str,
    max_tokens: int,
    json_mode: bool = True,
) -> str:
    url = base_url.rstrip("/") + "/chat/completions"
    payload: dict = {
        "model": model,
        "temperature": 0.3,
        "max_tokens": max_tokens,
        "messages": messages,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handle = tempfile.NamedTemporaryFile(prefix="signals-ai-", suffix=".json", delete=False)
    payload_path = handle.name
    handle.write(body)
    handle.close()
    response_path = payload_path + ".out"
    command = [
        "curl", "-sS", "--max-time", "150", "-X", "POST", url,
        "-H", f"Authorization: Bearer {api_key}",
        "-H", "Content-Type: application/json",
        "-H", "Accept: application/json",
        "-H", "HTTP-Referer: https://bunnyzjm.github.io/shipping-signals/",
        "-H", "X-Title: Shipping Signals",
        "--data-binary", f"@{payload_path}",
        "-o", response_path,
        "-w", "%{http_code}",
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=170)
        status_text = result.stdout.strip()
        status = int(status_text) if status_text.isdigit() else 0
        raw = Path(response_path).read_text(encoding="utf-8", errors="replace") if Path(response_path).exists() else ""
        lib.log(f"模型 {model}：HTTP {status}，{len(raw)} 字节")
        if status == 400 and json_mode:
            lib.log("模型不接受 response_format，改为从文本中解析 JSON 重试")
            return call_model(messages, api_key, base_url, model, max_tokens, json_mode=False)
        if status == 0:
            raise ApiError(f"网络请求失败（{result.stderr.strip()[:200] or '无响应'}）", 0)
        if not 200 <= status < 300:
            raise ApiError(f"模型返回 HTTP {status}：{raw[:400]}", status)
        if not raw.strip():
            raise ApiError(f"模型返回 HTTP {status} 但响应体为空", status)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ApiError(f"响应不是 JSON（HTTP {status}）：{raw[:300]}", status) from exc
        if isinstance(data, dict) and data.get("error"):
            error = data["error"]
            message = error.get("message") if isinstance(error, dict) else error
            raise ApiError(f"模型返回错误：{str(message)[:300]}", status)
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ApiError(f"响应结构异常（HTTP {status}）：{raw[:300]}", status) from exc
        usage = data.get("usage") or {}
        if usage:
            lib.log(f"token 用量：{usage}")
        if not (content or "").strip():
            raise ApiError("模型返回空内容", status)
        return content
    finally:
        Path(payload_path).unlink(missing_ok=True)
        Path(response_path).unlink(missing_ok=True)


class Composer:
    """按顺序尝试模型链：限流等 30 秒重试，报错就换下一个模型。"""

    def __init__(self, models: list[str], api_key: str, base_url: str) -> None:
        self.models = models
        self.api_key = api_key
        self.base_url = base_url
        self.index = 0
        self.calls = 0
        self.used: list[str] = []

    def chat(self, messages: list[dict], max_tokens: int) -> tuple[str, str]:
        last_error: Exception | None = None
        while self.index < len(self.models):
            model = self.models[self.index]
            for attempt in (1, 2):
                if self.calls >= MAX_CALLS:
                    raise RuntimeError(f"本次运行调用次数已达上限 {MAX_CALLS} 次")
                self.calls += 1
                try:
                    content = call_model(messages, self.api_key, self.base_url, model, max_tokens)
                    if model not in self.used:
                        self.used.append(model)
                    return content, model
                except ApiError as exc:
                    last_error = exc
                    if exc.status == 429:
                        lib.log("触发限流，等待 30 秒后重试")
                        time.sleep(30)
                        continue
                    if exc.status >= 500 or exc.status == 0:
                        lib.log(f"{model} 暂时不可用，5 秒后重试")
                        time.sleep(5)
                        continue
                    lib.log(f"{model} 不可用：{str(exc)[:200]}")
                    break
                except Exception as exc:  # 超时、解析失败等
                    last_error = exc
                    lib.log(f"{model} 调用失败：{str(exc)[:200]}")
                    break
            self.index += 1
            if self.index < len(self.models):
                lib.log(f"切换备用模型：{self.models[self.index]}")
        raise RuntimeError(f"所有模型均不可用：{last_error}")


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


# ---------------------------------------------------------------- 成稿

def clean_list(values, limit: int, item_limit: int = 110, min_len: int = 0) -> list[str]:
    result = []
    for value in values or []:
        text = lib.truncate(str(value).strip(), item_limit)
        if len(text) < min_len:
            continue
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def accept_items(
    raw_items: list[dict],
    by_id: dict[str, dict],
    board_id: str,
    used_ids: set[str],
    issue: int,
    limit: int,
) -> list[dict]:
    """把模型输出变成站内条目；不合格的单条直接丢弃，宁缺毋滥。"""
    accepted: list[dict] = []
    for raw in raw_items or []:
        if not isinstance(raw, dict):
            continue
        candidate_id = str(raw.get("candidateId") or raw.get("id") or "").strip()
        candidate = by_id.get(candidate_id)
        if not candidate or candidate.get("board") != board_id:
            continue
        if candidate_id in used_ids:
            continue
        summary = lib.truncate(str(raw.get("summary", "")).strip(), 320)
        why = lib.truncate(str(raw.get("whyMatters", "")).strip(), 260)
        implications = clean_list(raw.get("implications"), 4, 110, min_len=10)
        actions = clean_list(raw.get("actions"), 4, 110, min_len=10)
        tags = clean_list(raw.get("tags"), 4, 16, min_len=2)
        if len(summary) < 60 or len(why) < 30:
            lib.log(f"丢弃 {candidate_id}：摘要或解读过短")
            continue
        if len(implications) < 2 or len(actions) < 2 or len(tags) < 2:
            lib.log(f"丢弃 {candidate_id}：启示/动作/标签不完整")
            continue
        importance = str(raw.get("importance", "medium")).lower()
        if importance not in {"high", "medium", "low"}:
            importance = "medium"
        number = len(accepted) + 1
        accepted.append({
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
        used_ids.add(candidate_id)
        if len(accepted) >= limit:
            break
    return accepted


def board_prompt(
    board_id: str,
    board_name: str,
    candidates: list[dict],
    edition_date: date,
    low: int,
    high: int,
    repair: bool,
) -> list[dict]:
    window = lib.week_window(edition_date)
    user = (
        f"板块：{board_name}（{board_id}）\n"
        f"周报日期：{lib.iso_date(edition_date)}，收录窗口：{window['from']} 至 {window['to']}\n"
        f"请从下列候选材料中挑选最多 {high} 条要闻（至少 {low} 条）：候选里相关且在本周窗口内的尽量都选上，"
        "相关性弱的不要选，也不要重复。未抓取原文的 verified=false 材料少用。\n\n"
        f"候选材料（JSON 数组）：\n{json.dumps(candidates, ensure_ascii=False, indent=1)}\n\n"
        f"严格按以下结构输出：\n{ITEM_SCHEMA}"
    )
    if repair:
        user += (
            f"\n\n注意：上一版可用条目不足，请务必输出至少 {low} 条，"
            "每条 summary 不少于 100 字，implications 和 actions 各不少于 2 条且每条不少于 12 字。"
        )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def request_board_items(
    composer: Composer,
    board_id: str,
    board_name: str,
    candidates: list[dict],
    edition_date: date,
    low: int,
    high: int,
    repair: bool = False,
) -> list[dict]:
    messages = board_prompt(board_id, board_name, candidates, edition_date, low, high, repair)
    content, model = composer.chat(messages, max_tokens=6000)
    lib.log(f"{board_name}：{model} 返回 {len(content)} 字符")
    try:
        data = parse_json_content(content)
    except json.JSONDecodeError as exc:
        raise ApiError(f"{board_name} 返回内容无法解析为 JSON：{content[:200]}") from exc
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise ApiError(f"{board_name} 返回结构缺少 items 数组：{content[:200]}")
    return items


def compose_items(
    composer: Composer,
    board_map: dict[str, list[dict]],
    edition_date: date,
    board_names: dict[str, str],
    limits: dict,
) -> tuple[list[dict], dict[str, int]]:
    issue = lib.iso_week(edition_date)
    max_per_board = int(limits.get("weeklyMaxPerBoard", 5))
    max_total = int(limits.get("weeklyMaxItems", 35))
    min_total = int(limits.get("weeklyMinItems", 21))
    by_id = {candidate["id"]: candidate for entries in board_map.values() for candidate in entries}
    used: set[str] = set()
    items: list[dict] = []

    for board_id, candidates in board_map.items():
        if len(items) >= max_total:
            break
        board_name = board_names.get(board_id, board_id)
        high = min(max_per_board, len(candidates))
        low = min(3, high)
        accepted: list[dict] = []
        try:
            raw_items = request_board_items(composer, board_id, board_name, candidates, edition_date, low, high)
        except Exception as exc:
            # 输出被截断或 JSON 不合法时，按更小的目标再要一次
            lib.log(f"{board_name} 首次生成失败（{str(exc)[:160]}），精简重试一次")
            try:
                raw_items = request_board_items(
                    composer, board_id, board_name, candidates, edition_date, 3, min(4, high), repair=True
                )
            except Exception as retry_exc:
                lib.log(f"{board_name} 生成失败：{str(retry_exc)[:200]}")
                raw_items = []
        try:
            accepted = accept_items(raw_items, by_id, board_id, used, issue, high)
            # 可用条目不足 3 条时，请模型针对剩余候选补一次
            if len(accepted) < 3 and len(candidates) - len(accepted) >= 2:
                remaining = [c for c in candidates if c["id"] not in used]
                lib.log(f"{board_name}：首轮仅 {len(accepted)} 条可用，补选一次")
                raw_more = request_board_items(
                    composer, board_id, board_name, remaining, edition_date, 3, high, repair=True
                )
                accepted += accept_items(raw_more, by_id, board_id, used, issue, max(1, high - len(accepted)))
        except Exception as exc:
            lib.log(f"{board_name} 补选失败：{str(exc)[:200]}")
        if accepted:
            lib.log(f"{board_name}：收录 {len(accepted)} 条")
        items.extend(accepted[: max(0, max_total - len(items))])

    # 总数不足时，向还有剩余候选的板块补选
    topups = 0
    while len(items) < min_total and topups < 5:
        board_id = None
        remaining: list[dict] = []
        for candidate_board, candidates in board_map.items():
            rest = [c for c in candidates if c["id"] not in used]
            current = sum(1 for item in items if item["board"] == candidate_board)
            if len(rest) > len(remaining) and current < max_per_board:
                board_id, remaining = candidate_board, rest
        if not board_id or not remaining:
            break
        topups += 1
        board_name = board_names.get(board_id, board_id)
        try:
            raw_more = request_board_items(
                composer, board_id, board_name, remaining, edition_date, 1, max_per_board, repair=True
            )
            extra = accept_items(raw_more, by_id, board_id, used, issue, max_per_board)
        except Exception as exc:
            lib.log(f"{board_name} 补选失败：{str(exc)[:200]}")
            extra = []
        if not extra:
            continue
        items.extend(extra[: max(0, max_total - len(items))])

    # 重新编号，保证同一板块 id 连续
    counter: dict[str, int] = {}
    for item in items:
        counter[item["board"]] = counter.get(item["board"], 0) + 1
        item["id"] = f"c{issue}-{item['board']}-{counter[item['board']]}"
    board_order = {board_id: index for index, board_id in enumerate(board_names)}
    items.sort(key=lambda item: (board_order.get(item["board"], 99), item["id"]))
    return items, counter


def fallback_narrative(items: list[dict], board_names: dict[str, str]) -> tuple[str, str]:
    counts = Counter(item["board"] for item in items)
    tags = Counter(tag for item in items for tag in item["tags"])
    top_tags = [tag for tag, _ in tags.most_common(3)]
    title = f"{len(counts)} 个板块 {len(items)} 条信号"
    if top_tags:
        title += f"：{'、'.join(top_tags[:2])}"
    parts = [f"{board_names.get(board, board)} {count} 条" for board, count in counts.most_common()]
    intro = f"本期覆盖 {len(counts)} 个业务板块、共 {len(items)} 条要闻：{'，'.join(parts)}。"
    if top_tags:
        intro += f"高频关键词：{'、'.join(top_tags)}。"
    intro += "所有条目均来自公开报道，可点击原文核对。"
    return lib.truncate(title, 40), lib.truncate(intro, 220)


def compose_narrative(composer: Composer, items: list[dict], board_names: dict[str, str]) -> tuple[str, str]:
    headlines = [
        {"board": board_names.get(item["board"], item["board"]), "title": item["title"]}
        for item in items
    ]
    prompt = (
        "以下是本期周报已选定的要闻，请为整期写主题和导语。\n"
        f"要闻（JSON）：\n{json.dumps(headlines, ensure_ascii=False)}\n\n"
        '只输出 JSON：{"title": "本周主题，12–22 个汉字", "intro": "80–140 字，概括本周几条主线与共同关键词"}'
    )
    try:
        content, _ = composer.chat(
            [
                {"role": "system", "content": "你是航运物流媒体的标题编辑，只输出 JSON。"},
                {"role": "user", "content": prompt},
            ],
            max_tokens=500,
        )
        data = parse_json_content(content)
        title = lib.truncate(str(data.get("title", "")).strip(), 40)
        intro = lib.truncate(str(data.get("intro", "")).strip(), 220)
        if len(title) >= 6 and len(intro) >= 40:
            return title, intro
        lib.log("主题/导语不合格，改用模板生成")
    except Exception as exc:
        lib.log(f"主题/导语生成失败：{str(exc)[:160]}，改用模板")
    return fallback_narrative(items, board_names)


def build_edition(
    items: list[dict],
    title: str,
    intro: str,
    edition_date: date,
    models_used: list[str],
) -> dict:
    return {
        "id": lib.iso_date(edition_date),
        "issue": lib.iso_week(edition_date),
        "date": lib.iso_date(edition_date),
        "publishedAt": f"{lib.iso_date(edition_date)}T08:00:00+08:00",
        "window": lib.week_window(edition_date),
        "title": title or "本周航运物流信号",
        "intro": intro,
        "items": items,
        "briefs": [],
        "generatedBy": {
            "mode": "ai",
            "model": ", ".join(models_used) if models_used else "unknown",
            "generatedAt": lib.now_iso(),
            "source": "automation/compose.py",
        },
    }


def write_step_summary(lines: list[str]) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    try:
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    except OSError:
        pass


def main() -> int:
    args = parse_args()
    edition_date = lib.parse_date(args.date) if args.date else lib.today_bj()
    if not edition_date:
        raise SystemExit("--date 需要 YYYY-MM-DD 格式")

    candidates_path = Path(args.candidates)
    if not candidates_path.exists():
        raise SystemExit(f"候选文件不存在：{candidates_path}，请先运行 collect.py")
    candidates_doc = lib.read_json(candidates_path)
    board_map = group_candidates(candidates_doc.get("candidates", []), args.max_candidates)
    total_candidates = sum(len(entries) for entries in board_map.values())
    if len(board_map) < 3 or total_candidates < 12:
        print(f"材料不足：仅 {len(board_map)} 个板块、{total_candidates} 条可用候选，未生成周报。", flush=True)
        print("建议检查 automation/candidates.json、扩大回看窗口，或在 automation/inbox.json 手动补充链接。", flush=True)
        return 3

    boards = lib.load_boards()
    board_names = {board_id: board.get("name", board_id) for board_id, board in boards.items()}
    limits = lib.load_config().get("settings", {})
    max_per_board = int(limits.get("weeklyMaxPerBoard", 5))
    min_total = int(limits.get("weeklyMinItems", 21))

    out_path = Path(args.out) if args.out else lib.EDITIONS_DIR / f"{lib.iso_date(edition_date)}.json"
    draft_path = Path(args.draft_out) if args.draft_out else lib.DRAFTS_DIR / f"edition-{lib.iso_date(edition_date)}.json"

    try:
        if args.from_json:
            lib.log(f"读取离线生成结果 {args.from_json}")
            data = lib.read_json(Path(args.from_json))
            issue = lib.iso_week(edition_date)
            by_id = {c["id"]: c for entries in board_map.values() for c in entries}
            used: set[str] = set()
            grouped: dict[str, list[dict]] = {}
            for raw in data.get("items") or []:
                candidate = by_id.get(str(raw.get("candidateId", "")))
                if candidate:
                    grouped.setdefault(candidate["board"], []).append(raw)
            items: list[dict] = []
            for board_id, raw_items in grouped.items():
                items.extend(accept_items(raw_items, by_id, board_id, used, issue, max_per_board))
            board_order = {board_id: index for index, board_id in enumerate(board_names)}
            items.sort(key=lambda item: (board_order.get(item["board"], 99), item["id"]))
            title = lib.truncate(str(data.get("title", "")).strip(), 40)
            intro = lib.truncate(str(data.get("intro", "")).strip(), 220)
            if not title or not intro:
                title, intro = fallback_narrative(items, board_names)
            edition = build_edition(items, title, intro, edition_date, ["offline-json"])
        else:
            api_key = (os.environ.get("AI_API_KEY") or "").strip()
            if not api_key:
                message = (
                    "未配置 AI_API_KEY：请到仓库 Settings → Secrets and variables → Actions "
                    "添加 Repository secret「AI_API_KEY」，值为 OpenRouter 的 API Key。"
                )
                print(message, flush=True)
                write_step_summary([f"### 缺少 AI_API_KEY\n\n{message}"])
                return 4
            models = model_chain(args.model or os.environ.get("AI_MODEL"))
            lib.log(f"模型链：{', '.join(models)}；候选 {total_candidates} 条，覆盖 {len(board_map)} 个板块")
            composer = Composer(models, api_key, args.base_url)
            items, counts = compose_items(composer, board_map, edition_date, board_names, limits)
            lib.log("成稿 " + str(len(items)) + " 条：" + "，".join(f"{board_names.get(b, b)} {n}" for b, n in counts.items()))
            if len(items) < min_total:
                message = (
                    f"只生成 {len(items)} 条可用要闻（要求至少 {min_total} 条），本次不发布。"
                    "通常是个别模型输出不合格或候选材料不足，可以重跑一次。"
                )
                print(message, flush=True)
                write_step_summary([f"### 条目不足\n\n{message}"])
                return 2
            title, intro = compose_narrative(composer, items, board_names)
            edition = build_edition(items, title, intro, edition_date, composer.used)
    except Exception as exc:
        message = f"AI 生成失败：{exc}"
        print(message, flush=True)
        write_step_summary([f"### AI 生成失败\n\n```\n{message[:1500]}\n```"])
        return 4

    if len(edition["items"]) < min_total:
        print(f"成稿只有 {len(edition['items'])} 条，未达 {min_total} 条下限，不发布。", flush=True)
        return 2

    lib.write_json(draft_path, edition)
    errors = lib.validate_edition(edition, boards, strict=True)
    if errors:
        print("草稿未通过校验，已保留在 automation/drafts/，不会发布：", flush=True)
        for error in errors:
            print(f"  - {error}", flush=True)
        write_step_summary(["### 草稿未通过校验"] + [f"- {error}" for error in errors[:20]])
        return 2
    if args.dry_run:
        lib.log(f"dry-run：草稿已写入 {draft_path}")
        return 0
    lib.write_json(out_path, edition)
    lib.log(f"已写入 {out_path}（{len(edition['items'])} 条要闻）")
    write_step_summary([
        "### 本期周报已生成",
        f"- 共 {len(edition['items'])} 条要闻，覆盖 {len({item['board'] for item in edition['items']})} 个板块",
        f"- 标题：{edition['title']}",
        f"- 使用模型：{edition['generatedBy']['model']}",
    ])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
