#!/usr/bin/env python3
"""航策 · 共享工具：抓取、解析、日期、索引与数据校验。

只使用 Python 标准库 + 系统 curl，避免第三方依赖，保证 GitHub Actions
与本地环境行为一致。所有网络内容都视为不可信输入，写入站点前会做校验。
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
EDITIONS_DIR = DATA_DIR / "editions"
REPORTS_DIR = DATA_DIR / "reports"
INDEX_PATH = DATA_DIR / "index.json"
CONFIG_PATH = ROOT / "config" / "sources.json"
AUTOMATION_DIR = ROOT / "automation"
ARTICLES_DIR = AUTOMATION_DIR / "articles"
DRAFTS_DIR = AUTOMATION_DIR / "drafts"

BEIJING = timezone(timedelta(hours=8))
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
NAV_WORDS = (
    "首页", "登录", "注册", "更多", "下载", "关于我们", "联系方式", "广告", "招聘",
    "订阅", "免责声明", "版权", "English", "网站地图", "返回首页", "设为首页",
    "加入收藏", "会员中心", "用户协议", "隐私政策", "APP", "客户端",
)


# ---------------------------------------------------------------- 基础 IO

def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def load_config() -> dict:
    return read_json(CONFIG_PATH)


def now_bj() -> datetime:
    return datetime.now(BEIJING)


def today_bj() -> date:
    return now_bj().date()


def now_iso() -> str:
    return now_bj().replace(microsecond=0).isoformat()


def safe_filename(value: str, limit: int = 60) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-")
    return (cleaned or "item")[:limit]


# ---------------------------------------------------------------- 日期

def parse_date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    text = str(value).strip()
    match = re.match(r"^(20\d{2})-(\d{1,2})-(\d{1,2})", text)
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            return None
    for fmt in ("%Y/%m/%d", "%Y.%m.%d", "%Y年%m月%d日"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    try:
        parsed = parsedate_to_datetime(text)
        if parsed:
            return parsed.astimezone(BEIJING).date()
    except (TypeError, ValueError, OverflowError):
        pass
    return None


def iso_date(value: date | datetime) -> str:
    return value.strftime("%Y-%m-%d")


def parse_text_date(text: str, reference: date | None = None, allow_short: bool = True) -> date | None:
    """从标题/正文中解析日期，兼容中文站点常见写法。

    allow_short=False 时不再解析 11-13 这类短日期，避免把正文里的
    “3-5 条”等数字误判为日期。
    """
    ref = reference or today_bj()
    if not text:
        return None
    text = unescape(text)
    match = re.search(r"(20\d{2})\s*[年\-/.]\s*(\d{1,2})\s*[月\-/.]\s*(\d{1,2})", text)
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            return None
    if re.search(r"\d+\s*(分钟|小时)\s*前", text):
        return ref
    match = re.search(r"(\d+)\s*天前", text)
    if match:
        return ref - timedelta(days=int(match.group(1)))
    if "昨天" in text:
        return ref - timedelta(days=1)
    if "前天" in text:
        return ref - timedelta(days=2)
    if not allow_short:
        return None
    # 11-13 / 11.13 / 11/13
    match = re.search(r"(?<!\d)(\d{1,2})\s*[-/.]\s*(\d{1,2})(?!\d)", text)
    if match:
        month, day = int(match.group(1)), int(match.group(2))
        if 1 <= month <= 12 and 1 <= day <= 31:
            try:
                candidate = date(ref.year, month, day)
            except ValueError:
                return None
            if candidate > ref + timedelta(days=7):
                try:
                    candidate = date(ref.year - 1, month, day)
                except ValueError:
                    return None
            return candidate
    return None


def extract_url_date(url: str) -> date | None:
    patterns = (
        r"/(20\d{2})[/_-](\d{2})[/_-](\d{2})(?:[/_.-]|$)",
        r"/(20\d{2})/(\d{2})(\d{2})/",
        r"t(20\d{2})(\d{2})(\d{2})_",
        r"/(20\d{2})/[^/?#]*?_(\d{2})(\d{2})/",
        r"/(20\d{2})(\d{2})/(\d{2})/",
        r"/(20\d{2})(\d{2})(\d{2})/",
    )
    for pattern in patterns:
        match = re.search(pattern, url)
        if not match:
            continue
        try:
            candidate = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            continue
        if 2000 <= candidate.year <= today_bj().year + 1:
            return candidate
    return None


def iso_week(date_value: date) -> int:
    return date_value.isocalendar().week


def week_window(edition_date: date) -> dict:
    return {
        "from": iso_date(edition_date - timedelta(days=6)),
        "to": iso_date(edition_date),
    }


def next_monday(after: date) -> date:
    days = (7 - after.weekday()) % 7
    if days == 0:
        days = 7
    return after + timedelta(days=days)


def next_update_iso(after: date | None = None) -> str:
    base = after or today_bj()
    target = next_monday(base)
    return f"{iso_date(target)}T08:00:00+08:00"


# ---------------------------------------------------------------- 网络

@dataclass
class FetchResult:
    url: str
    final_url: str
    status: int
    body: bytes
    error: str = ""

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300 and not self.error


def curl_available() -> bool:
    try:
        subprocess.run(["curl", "--version"], capture_output=True, timeout=10, check=True)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def fetch(url: str, timeout: int = 30, max_bytes: int = 4_000_000) -> FetchResult:
    """用 curl 抓取页面，避开本地 Python 证书链差异，同时保留状态码。"""
    if not url.lower().startswith(("http://", "https://")):
        return FetchResult(url, url, 0, b"", "unsupported url")
    handle = tempfile.NamedTemporaryFile(prefix="signals-", delete=False)
    tmp_path = handle.name
    handle.close()
    command = [
        "curl", "-L", "--compressed", "-sS", "--max-time", str(timeout),
        "--connect-timeout", "15", "-A", UA,
        "-H", "Accept-Language: zh-CN,zh;q=0.9,en;q=0.8",
        "-o", tmp_path, "-w", "%{http_code}\t%{url_effective}", url,
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout + 15)
        meta = result.stdout.strip().split("\t")
        status = 0
        final_url = url
        if len(meta) >= 2:
            try:
                status = int(meta[0])
            except ValueError:
                status = 0
            final_url = meta[1].strip() or url
        path = Path(tmp_path)
        body = path.read_bytes()[:max_bytes] if path.exists() else b""
        error = result.stderr.strip()[:300] if result.returncode != 0 else ""
        return FetchResult(url, final_url, status, body, error)
    except subprocess.TimeoutExpired:
        return FetchResult(url, url, 0, b"", "timeout")
    except OSError as exc:
        return FetchResult(url, url, 0, b"", str(exc))
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def decode_bytes(raw: bytes) -> str:
    if not raw:
        return ""
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    head = raw[:5000].decode("ascii", "ignore").lower()
    match = re.search(r"charset=[\"']?([a-z0-9_-]+)", head)
    declared = match.group(1) if match else ""
    candidates = []
    if declared:
        candidates.append("gb18030" if declared in {"gb2312", "gbk"} else declared)
    candidates.extend(["utf-8", "gb18030"])
    for candidate in candidates:
        try:
            return raw.decode(candidate)
        except (LookupError, UnicodeDecodeError):
            continue
    return raw.decode("utf-8", "replace")


# ---------------------------------------------------------------- HTML 解析

class _TextParser(HTMLParser):
    def __init__(self, tags: tuple[str, ...]) -> None:
        super().__init__(convert_charrefs=True)
        self.tags = tags
        self.parts: list[str] = []
        self._depth = 0
        self._skip = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"script", "style", "nav", "header", "footer", "aside", "form", "svg", "noscript"}:
            self._skip += 1
            return
        if self._skip:
            return
        if tag in self.tags:
            self._depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "nav", "header", "footer", "aside", "form", "svg", "noscript"}:
            self._skip = max(0, self._skip - 1)
            return
        if self._skip:
            return
        if tag in self.tags and self._depth:
            self._depth -= 1
            if self._depth == 0:
                self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip and self._depth:
            text = " ".join(data.split())
            if text:
                self.parts.append(text)


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self.meta: dict[str, str] = {}
        self._href: str | None = None
        self._text: list[str] = []
        self._title_attr = ""

    def handle_starttag(self, tag: str, attrs) -> None:
        values = {key.lower(): (value or "") for key, value in attrs}
        if tag == "a":
            href = values.get("href", "").strip()
            if href:
                self._href = href
                self._text = []
                self._title_attr = values.get("title", "").strip()
        elif tag == "meta":
            name = (values.get("property") or values.get("name") or "").lower()
            content = values.get("content", "").strip()
            if name and content and name not in self.meta:
                self.meta[name] = content

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href is not None:
            text = " ".join(" ".join(self._text).split())
            if not text:
                text = self._title_attr
            self.links.append((self._href, text))
            self._href = None
            self._text = []


class _ArticleParser(HTMLParser):
    """抽取正文段落。只收集 p 与标题，避免把导航/列表整块吞进正文。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.paragraphs: list[str] = []
        self.all_text: list[str] = []
        self._in_paragraph = 0
        self._in_heading = 0
        self._skip = 0
        self._buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in {"script", "style", "nav", "header", "footer", "aside", "form", "svg", "noscript"}:
            self._skip += 1
            return
        if self._skip:
            return
        if tag == "p":
            self._in_paragraph += 1
            self._buffer = []
        elif tag in {"h1", "h2", "h3"}:
            self._in_heading += 1
            self._buffer = []

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "nav", "header", "footer", "aside", "form", "svg", "noscript"}:
            self._skip = max(0, self._skip - 1)
            return
        if self._skip:
            return
        if tag == "p" and self._in_paragraph:
            self._in_paragraph -= 1
            if self._in_paragraph == 0:
                text = " ".join(" ".join(self._buffer).split())
                if len(text) >= 15:
                    self.paragraphs.append(text)
                self._buffer = []
        elif tag in {"h1", "h2", "h3"} and self._in_heading:
            self._in_heading -= 1
            text = " ".join(" ".join(self._buffer).split())
            if text:
                self.all_text.append(text)
            self._buffer = []

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        text = " ".join(data.split())
        if not text:
            return
        self.all_text.append(text)
        if self._in_paragraph or self._in_heading:
            self._buffer.append(text)


def strip_html(fragment: str, limit: int = 0) -> str:
    if not fragment:
        return ""
    text = ""
    if "<" in fragment:
        parser = _TextParser(("p", "div", "span", "li", "h1", "h2", "h3", "strong", "em"))
        try:
            parser.feed(fragment)
            text = " ".join(" ".join(parser.parts).split())
        except Exception:
            text = ""
        if not text:
            text = re.sub(r"<[^>]+>", " ", fragment)
    else:
        text = fragment
    text = re.sub(r"\s+", " ", unescape(text)).strip()
    return text[:limit] if limit else text


RELATED_MARKERS = (
    "相关阅读", "相关新闻", "推荐阅读", "延伸阅读", "热门推荐", "更多精彩", "责任编辑",
    "免责声明", "相关报道", "Related articles", "Read more", "Sign up", "Subscribe",
    "More from", "Latest news", "You may also like", "Contact us",
)
NAV_TOKENS = (
    "home", "login", "register", "newsletter", "contact", "advertise", "subscribe",
    "privacy policy", "terms of", "forgot your password", "magazine", "premium",
)


def looks_boilerplate(line: str) -> bool:
    text = line.strip()
    if not text:
        return True
    lowered = text.lower()
    if len(text) < 600 and sum(token in lowered for token in NAV_TOKENS) >= 3:
        return True
    # 英文站点的行情跑马灯，如 “XPO: TOP PICK MAERSK: DOWNGRADED ...”
    if len(text) < 600 and len(re.findall(r"\b[A-Z]{2,5}:", text)) >= 4:
        return True
    if re.match(r"^\s*20\d{2}[-/.]\d{1,2}[-/.]\d{1,2}\s+\d{1,2}:\d{2}", text) and ("来源" in text or "编辑" in text or "来源：" in text):
        return True
    if len(text) < 120 and any(marker in text for marker in ("责任编辑：", "免责声明", "版权所有", "Copyright ©", "All rights reserved")):
        return True
    if len(text) < 60 and any(lowered.startswith(marker.lower()) for marker in RELATED_MARKERS):
        return True
    return False


def clean_title(text: str) -> str:
    text = " ".join(unescape(text or "").split())
    text = re.sub(r"\s*[-_|·]\s*(航运界|国际船舶网|港口圈|中国物流与采购网|交通运输部|中国海事局|Lloyd's List|gCaptain|Splash 247|The Loadstar|FreightWaves)\s*$", "", text, flags=re.I)
    text = re.sub(r"\s*\d+\s*(分钟|小时|天)前\s*$", "", text)
    text = re.sub(r"\s*(昨天|前天)\s*$", "", text)
    text = re.sub(r"\s*20\d{2}年\d{1,2}月\d{1,2}日\s*$", "", text)
    text = re.sub(r"\s+20\d{2}[-/.]\d{1,2}[-/.]\d{1,2}\s*$", "", text)
    text = re.sub(r"\s+\d{1,2}[-/.]\d{1,2}\s*$", "", text)
    return text.strip(" -_|·\t")


def extract_links(html: str, page_url: str) -> list[dict]:
    parser = _LinkParser()
    try:
        parser.feed(html)
    except Exception:
        return []
    page_host = urlparse(page_url).netloc.lower()
    seen: set[str] = set()
    links: list[dict] = []
    for href, text in parser.links:
        if not text:
            continue
        absolute = urljoin(page_url, href.strip())
        if not absolute.lower().startswith(("http://", "https://")):
            continue
        clean = normalize_url(absolute)
        if clean in seen:
            continue
        seen.add(clean)
        host = urlparse(clean).netloc.lower()
        if not (host == page_host or host.endswith("." + page_host) or page_host.endswith("." + host)):
            continue
        title = clean_title(text)
        if len(title) < 8 or len(title) > 110:
            continue
        if any(word in title for word in NAV_WORDS):
            continue
        links.append({"url": clean, "title": title})
    return links


def article_extract(html: str, url: str) -> dict:
    link_parser = _LinkParser()
    try:
        link_parser.feed(html)
    except Exception:
        pass
    meta = link_parser.meta
    parser = _ArticleParser()
    try:
        parser.feed(html)
    except Exception:
        pass
    paragraphs: list[str] = []
    for paragraph in parser.paragraphs:
        if any(paragraph.lower().startswith(marker.lower()) for marker in RELATED_MARKERS):
            break
        # 中文站点常在正文末尾拼接“相关阅读”摘要，以省略号 + 时间结尾，遇到即截断
        if re.search(r"(\.\.\.|…)\s*20\d{2}[-/.]\d{1,2}[-/.]\d{1,2}\s+\d{1,2}:\d{2}", paragraph):
            break
        if re.match(r"^(关于我们|网站地图|联系方式)\s*[|｜]", paragraph):
            break
        if len(paragraph) >= 25 and not looks_boilerplate(paragraph):
            paragraphs.append(paragraph)
    text = "\n".join(paragraphs)
    if len(text) < 300:
        lines = [
            line for line in parser.all_text
            if len(line) >= 40 and not looks_boilerplate(line)
        ]
        fallback = "\n".join(lines)
        if len(fallback) > len(text):
            text = fallback
    text = text[:8000]
    title = meta.get("og:title") or meta.get("twitter:title") or ""
    if not title:
        title_match = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.S | re.I)
        title = strip_html(title_match.group(1)) if title_match else ""
    description = meta.get("og:description") or meta.get("description") or ""
    published = (
        meta.get("article:published_time")
        or meta.get("og:published_time")
        or meta.get("publishdate")
        or meta.get("pubdate")
        or ""
    )
    published_date = parse_date(published) or parse_text_date(text[:3000], allow_short=False) or extract_url_date(url)
    return {
        "title": clean_title(title),
        "description": " ".join(unescape(description).split())[:500],
        "text": text,
        "publishedAt": iso_date(published_date) if published_date else "",
    }


def normalize_url(url: str) -> str:
    parsed = urlparse(url.strip())
    query = [
        (key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith(("utm_", "spm", "from", "share", "scene", "clicktime"))
    ]
    path = re.sub(r"/(index|default)\.(html?|shtml)$", "/", parsed.path)
    return urlunparse((parsed.scheme, parsed.netloc.lower(), path, "", urlencode(query), ""))


# ---------------------------------------------------------------- RSS / Atom

def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def parse_feed(raw: bytes) -> list[dict]:
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return []
    items = []
    if _local_name(root.tag) == "rss":
        items = [child for child in root.iter() if _local_name(child.tag) == "item"]
    else:
        items = [child for child in root.iter() if _local_name(child.tag) == "entry"]
    entries: list[dict] = []
    for item in items:
        values: dict[str, Any] = {}
        link = ""
        for child in list(item):
            name = _local_name(child.tag)
            if name == "link":
                href = child.attrib.get("href")
                rel = child.attrib.get("rel", "alternate")
                if href and (rel == "alternate" or not link):
                    link = href
                if child.text and not href and not link:
                    link = child.text.strip()
            elif name in {"title", "description", "summary", "content", "encoded", "pubdate", "published", "updated"}:
                text = "".join(child.itertext())
                if name not in values:
                    values[name] = text
        title = clean_title(strip_html(values.get("title", "")))
        if not title or not link:
            continue
        summary = strip_html(values.get("description") or values.get("summary") or values.get("content") or values.get("encoded") or "", 500)
        published_raw = values.get("pubdate") or values.get("published") or values.get("updated") or ""
        entries.append({
            "title": title,
            "url": normalize_url(link),
            "summary": summary,
            "publishedAt": iso_date(parse_date(published_raw)) if parse_date(published_raw) else "",
        })
    return entries


# ---------------------------------------------------------------- 分类

def _keyword_hit(text: str, keyword: str) -> bool:
    text_lower = text.lower()
    keyword_lower = keyword.lower()
    if re.search(r"[\u4e00-\u9fff]", keyword):
        return keyword_lower in text_lower
    return re.search(r"(?<![a-z0-9])" + re.escape(keyword_lower) + r"(?![a-z0-9])", text_lower) is not None


def classify(title: str, summary: str, config: dict, source: dict) -> tuple[str, int, list[str]]:
    boards_cfg = config.get("boards", {})
    scores: dict[str, int] = {}
    hits: dict[str, list[str]] = {}
    blob = f"{title} {summary}"
    for board_id, board_cfg in boards_cfg.items():
        score = 0
        matched: list[str] = []
        for keyword in board_cfg.get("keywords", []):
            if _keyword_hit(title, keyword):
                score += 3
                matched.append(keyword)
            elif _keyword_hit(summary, keyword):
                score += 1
                matched.append(keyword)
        for bad in board_cfg.get("exclude", []):
            if _keyword_hit(blob, bad):
                score -= 3
        if score > 0:
            scores[board_id] = score
            hits[board_id] = matched
    default = source.get("defaultBoard", "")
    if not scores:
        if default and source.get("keepUnmatched"):
            return default, 1, []
        return "", 0, []
    best = max(scores, key=lambda key: scores[key])
    if default and scores.get(best, 0) <= 2 and scores.get(default, 0) >= 0:
        best = default
    return best, scores.get(best, 0), hits.get(best, [])


# ---------------------------------------------------------------- 索引与校验

def load_boards() -> dict[str, dict]:
    if not INDEX_PATH.exists():
        return {}
    return {board["id"]: board for board in read_json(INDEX_PATH).get("boards", [])}


def edition_metadata(edition: dict) -> dict:
    edition_date = parse_date(edition.get("date")) or today_bj()
    issue = edition.get("issue") or iso_week(edition_date)
    items = edition.get("items", [])
    briefs = edition.get("briefs", [])
    board_order = {board_id: index for index, board_id in enumerate(load_boards())}
    boards = sorted(
        {item.get("board") for item in items + briefs if item.get("board")},
        key=lambda board_id: board_order.get(board_id, 99),
    )
    lead = items[0]["title"] if items else (briefs[0]["title"] if briefs else "")
    return {
        "id": edition.get("id") or iso_date(edition_date),
        "date": iso_date(edition_date),
        "issue": issue,
        "weekLabel": f"第 {issue} 周",
        "title": edition.get("title", ""),
        "intro": edition.get("intro", ""),
        "lead": lead,
        "itemCount": len(items),
        "briefCount": len(briefs),
        "publishedAt": edition.get("publishedAt", f"{iso_date(edition_date)}T08:00:00+08:00"),
        "window": edition.get("window") or week_window(edition_date),
        "boards": boards,
        "file": f"data/editions/{edition_date.isoformat()}.json",
    }


def report_metadata(report: dict) -> dict:
    return {
        "id": report.get("id") or report.get("month", ""),
        "month": report.get("month", ""),
        "title": report.get("title", ""),
        "status": report.get("status", "final"),
        "generatedAt": report.get("generatedAt", ""),
        "file": f"data/reports/{report.get('month', '')}.json",
    }


def rebuild_index() -> dict:
    index = read_json(INDEX_PATH) if INDEX_PATH.exists() else {"site": {}, "boards": [], "editions": [], "reports": []}
    editions: list[dict] = []
    for path in sorted(EDITIONS_DIR.glob("*.json")):
        if path.name.startswith(("draft", "_")):
            continue
        try:
            edition = read_json(path)
        except (json.JSONDecodeError, OSError):
            continue
        editions.append(edition_metadata(edition))
    editions.sort(key=lambda item: item["date"], reverse=True)

    reports: list[dict] = []
    for path in sorted(REPORTS_DIR.glob("*.json")):
        try:
            report = read_json(path)
        except (json.JSONDecodeError, OSError):
            continue
        reports.append(report_metadata(report))
    reports.sort(key=lambda item: item["month"], reverse=True)

    index["editions"] = editions
    index["reports"] = reports
    site = index.setdefault("site", {})
    if editions:
        latest = parse_date(editions[0]["date"])
        site["updatedAt"] = editions[0]["publishedAt"]
        site["nextUpdate"] = next_update_iso(latest)
    else:
        site["updatedAt"] = now_iso()
        site["nextUpdate"] = next_update_iso(today_bj())
    write_json(INDEX_PATH, index)
    return index


def validate_url(url: str) -> str | None:
    if not isinstance(url, str) or not url.strip():
        return "缺少链接"
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return f"链接不是有效的 http(s) 地址：{url[:80]}"
    if any(token in url.lower() for token in ("example.com", "localhost", "127.0.0.1")):
        return f"链接指向示例或本地地址：{url[:80]}"
    return None


PLACEHOLDER_WORDS = ("待补充", "TODO", "TBD", "示例文本", "占位", "lorem ipsum")


def validate_edition(edition: dict, boards: dict[str, dict], strict: bool = True) -> list[str]:
    errors: list[str] = []
    edition_id = edition.get("id", "?")
    for key in ("id", "date", "title", "intro", "items"):
        if not edition.get(key):
            errors.append(f"[{edition_id}] 缺少字段 {key}")
    edition_date = parse_date(edition.get("date"))
    if not edition_date:
        errors.append(f"[{edition_id}] date 不是有效日期")
    items = edition.get("items") or []
    briefs = edition.get("briefs") or []
    config = load_config()
    limits = config.get("settings", {})
    min_items = int(limits.get("weeklyMinItems", 3))
    max_items = int(limits.get("weeklyMaxItems", 5))
    per_board_since = parse_date(limits.get("perBoardSince"))
    per_board_mode = bool(per_board_since and edition_date and edition_date >= per_board_since)
    board_counts: dict[str, int] = {}
    for item in items:
        board_counts[item.get("board")] = board_counts.get(item.get("board"), 0) + 1
    if strict and per_board_mode:
        min_boards = int(limits.get("weeklyMinBoards", 6))
        max_per_board = int(limits.get("weeklyMaxPerBoard", 5))
        if not (min_items <= len(items) <= max_items):
            errors.append(f"[{edition_id}] 要闻总数应为 {min_items}–{max_items} 条（每板块 3–5 条），当前 {len(items)} 条")
        if len(board_counts) < min_boards:
            errors.append(f"[{edition_id}] 至少要覆盖 {min_boards} 个业务板块，当前 {len(board_counts)} 个")
        for board_id, count in board_counts.items():
            if count > max_per_board:
                errors.append(f"[{edition_id}] 板块 {board_id} 有 {count} 条，单板块上限 {max_per_board} 条")
            if count < 1:
                errors.append(f"[{edition_id}] 板块 {board_id} 条数异常")
    elif strict:
        if not (3 <= len(items) <= 5):
            errors.append(f"[{edition_id}] 要闻数量应为 3–5 条，当前 {len(items)} 条")
        if len(board_counts) < 3:
            errors.append(f"[{edition_id}] 至少要覆盖 3 个业务板块，当前 {len(board_counts)} 个")

    seen_ids: set[str] = set()
    seen_urls: set[str] = set()
    all_entries = [(item, "item") for item in items] + [(brief, "brief") for brief in briefs]
    for entry, kind in all_entries:
        entry_id = entry.get("id", "?")
        prefix = f"[{edition_id}/{entry_id}]"
        for key in ("id", "board", "title", "source", "sourceUrl", "summary"):
            if not entry.get(key):
                errors.append(f"{prefix} 缺少字段 {key}")
        if entry_id in seen_ids:
            errors.append(f"{prefix} id 重复")
        seen_ids.add(entry_id)
        if entry.get("board") not in boards:
            errors.append(f"{prefix} board 不存在：{entry.get('board')}")
        if parse_date(entry.get("publishedAt")) is None:
            errors.append(f"{prefix} publishedAt 无效：{entry.get('publishedAt')}")
        url_error = validate_url(entry.get("sourceUrl", ""))
        if url_error:
            errors.append(f"{prefix} {url_error}")
        else:
            normalized = normalize_url(entry["sourceUrl"])
            if normalized in seen_urls:
                errors.append(f"{prefix} 原文链接重复：{entry['sourceUrl']}")
            seen_urls.add(normalized)
        summary = str(entry.get("summary", ""))
        if len(summary) < 40:
            errors.append(f"{prefix} 摘要过短（{len(summary)} 字），需陈述完整事实")
        for bad in PLACEHOLDER_WORDS:
            if bad.lower() in (str(entry.get("title", "")) + str(summary) + str(entry.get("whyMatters", ""))).lower():
                errors.append(f"{prefix} 含占位文本「{bad}」")
        if kind == "item":
            for key in ("whyMatters", "implications", "actions", "tags", "importance"):
                if not entry.get(key):
                    errors.append(f"{prefix} 缺少字段 {key}")
            if entry.get("importance") not in {"high", "medium", "low"}:
                errors.append(f"{prefix} importance 应为 high / medium / low")
            for list_key in ("implications", "actions"):
                values = entry.get(list_key) or []
                if not isinstance(values, list) or len(values) < 2:
                    errors.append(f"{prefix} {list_key} 至少需要 2 条")
                elif any(len(str(value).strip()) < 10 for value in values):
                    errors.append(f"{prefix} {list_key} 存在过短条目")
            tags = entry.get("tags") or []
            if not isinstance(tags, list) or not 2 <= len(tags) <= 5:
                errors.append(f"{prefix} tags 应为 2–5 个")
            if not isinstance(entry.get("verified"), bool):
                errors.append(f"{prefix} verified 必须是布尔值")
        else:
            if len(summary) < 60:
                errors.append(f"{prefix} 简报摘要过短")
    return errors


def validate_report(report: dict, boards: dict[str, dict]) -> list[str]:
    errors: list[str] = []
    report_id = report.get("id", "?")
    for key in ("id", "month", "title", "status", "generatedAt", "executiveSummary", "keyJudgements", "signals", "boards", "actions", "method"):
        if not report.get(key):
            errors.append(f"[{report_id}] 缺少字段 {key}")
    if report.get("status") not in {"final", "month-to-date"}:
        errors.append(f"[{report_id}] status 应为 final 或 month-to-date")
    if parse_date(report.get("generatedAt")) is None:
        errors.append(f"[{report_id}] generatedAt 无效")
    if len(str(report.get("executiveSummary", ""))) < 120:
        errors.append(f"[{report_id}] executiveSummary 过短")
    if len(report.get("keyJudgements") or []) < 2:
        errors.append(f"[{report_id}] keyJudgements 至少 2 条")
    if not 3 <= len(report.get("signals") or []) <= 6:
        errors.append(f"[{report_id}] signals 应为 3–6 条")
    for signal in report.get("signals") or []:
        for key in ("label", "value", "unit"):
            if not signal.get(key):
                errors.append(f"[{report_id}] signal 缺少 {key}")
        if signal.get("tone") not in {"up", "down", "neutral"}:
            errors.append(f"[{report_id}] signal.tone 无效：{signal.get('tone')}")
    if not report.get("boards"):
        errors.append(f"[{report_id}] 缺少板块分析")
    seen_boards: set[str] = set()
    for section in report.get("boards") or []:
        board_id = section.get("board")
        if board_id not in boards:
            errors.append(f"[{report_id}] 板块不存在：{board_id}")
            continue
        if board_id in seen_boards:
            errors.append(f"[{report_id}] 板块重复：{board_id}")
        seen_boards.add(board_id)
        for key in ("headline", "summary", "trends", "implications", "watch", "related"):
            if not section.get(key):
                errors.append(f"[{report_id}/{board_id}] 缺少 {key}")
        for related in section.get("related") or []:
            for key in ("title", "source", "date", "url"):
                if not related.get(key):
                    errors.append(f"[{report_id}/{board_id}] related 缺少 {key}")
            url_error = validate_url(related.get("url", ""))
            if url_error:
                errors.append(f"[{report_id}/{board_id}] {url_error}")
    for action in report.get("actions") or []:
        for key in ("horizon", "action", "owner"):
            if not action.get(key):
                errors.append(f"[{report_id}] action 缺少 {key}")
    return errors


# ---------------------------------------------------------------- 其他

def group_by_board(entries: Iterable[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for entry in entries:
        grouped.setdefault(entry.get("board", ""), []).append(entry)
    return grouped


def truncate(text: str, limit: int, ellipsis: str = "…") -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: max(0, limit - 1)] + ellipsis


def log(message: str) -> None:
    print(f"[{now_bj().strftime('%H:%M:%S')}] {message}", flush=True)


def sleep(seconds: float) -> None:
    time.sleep(seconds)
