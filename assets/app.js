/* =========================================================
   航策 · Shipping Signals
   Zero-build interactive editorial site
   ========================================================= */

const DATA = {
  index: null,
  editions: [],
  reports: [],
  boardMap: {}
};

const state = {
  lastRouteKey: "",
  searchTimer: null
};

const CHECK_ICON = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 6 9 17l-5-5" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg>`;

const BOARD_EN = {
  container: "CONTAINER",
  chemical: "CHEMICAL",
  contract: "CONTRACT LOGISTICS",
  engineering: "PROJECT CARGO",
  air: "AIR FREIGHT",
  inspection: "INSPECTION",
  tally: "TALLY"
};

/* ---------- small utilities ---------- */
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;"
  }[char]));
}

function safeUrl(url) {
  try {
    const parsed = new URL(String(url), window.location.href);
    if (parsed.protocol === "http:" || parsed.protocol === "https:") return parsed.href;
  } catch (error) {
    /* ignore */
  }
  return "#";
}

function parseDate(value) {
  const parts = String(value || "").split("-").map(Number);
  if (parts.length !== 3 || parts.some(Number.isNaN)) return new Date();
  return new Date(parts[0], parts[1] - 1, parts[2]);
}

function fmtDate(value) {
  const date = parseDate(value);
  return `${date.getFullYear()}年${date.getMonth() + 1}月${date.getDate()}日`;
}

function fmtDateShort(value) {
  const date = parseDate(value);
  return `${String(date.getMonth() + 1).padStart(2, "0")}.${String(date.getDate()).padStart(2, "0")}`;
}

function weekdayCN(value) {
  const date = parseDate(value);
  return ["周日", "周一", "周二", "周三", "周四", "周五", "周六"][date.getDay()];
}

function pad2(value) {
  return String(value ?? "").padStart(2, "0");
}

function truncate(text, max = 160) {
  const value = String(text || "").trim();
  if (value.length <= max) return value;
  return `${value.slice(0, max).replace(/[，。；、\s]+$/, "")}…`;
}

function highlight(text, query) {
  const source = String(text ?? "");
  const needle = String(query || "").trim();
  if (!needle) return esc(source);
  const lowerSource = source.toLowerCase();
  const lowerNeedle = needle.toLowerCase();
  let cursor = 0;
  let output = "";
  while (cursor < source.length) {
    const index = lowerSource.indexOf(lowerNeedle, cursor);
    if (index === -1) {
      output += esc(source.slice(cursor));
      break;
    }
    output += esc(source.slice(cursor, index));
    output += `<mark>${esc(source.slice(index, index + needle.length))}</mark>`;
    cursor = index + needle.length;
  }
  return output;
}

function boardOf(id) {
  return DATA.boardMap[id] || { id, name: id, short: id, color: "#2f7d72", description: "" };
}

function boardTag(id) {
  const board = boardOf(id);
  return `<span class="board-tag" style="--b:${esc(board.color)}">${esc(board.name)}</span>`;
}

function boardDot(id) {
  const board = boardOf(id);
  return `<span class="board-dot" style="--b:${esc(board.color)}"></span>`;
}

function importanceLabel(value) {
  return value === "high" ? "高影响" : "关注";
}

function countBoards(edition) {
  const counts = {};
  [...(edition.items || []), ...(edition.briefs || [])].forEach((entry) => {
    counts[entry.board] = (counts[entry.board] || 0) + 1;
  });
  return counts;
}

function publishTime(iso) {
  const date = iso ? new Date(iso) : null;
  if (!date || Number.isNaN(date.getTime())) return "08:00";
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Shanghai"
  }).format(date);
}

function nextUpdateParts() {
  let iso = DATA.index?.site?.nextUpdate;
  if (!iso && DATA.editions.length) {
    const date = parseDate(DATA.editions[0].date);
    date.setDate(date.getDate() + 7);
    iso = date.toISOString();
  }
  if (!iso) return { label: "下周一 08:00", date: "下周一", time: "08:00" };
  const date = new Date(iso);
  const dateText = new Intl.DateTimeFormat("zh-CN", {
    month: "long", day: "numeric", timeZone: "Asia/Shanghai"
  }).format(date);
  const weekText = new Intl.DateTimeFormat("zh-CN", {
    weekday: "short", timeZone: "Asia/Shanghai"
  }).format(date);
  const timeText = publishTime(iso);
  return {
    label: `${dateText} · ${weekText} ${timeText}（北京时间）`,
    date: `${dateText} · ${weekText}`,
    time: timeText
  };
}

function updateDocumentTitle(title) {
  document.title = title ? `${title} | 航策` : "航策 · 航运物流情报站";
}

/* ---------- data loading ---------- */
async function fetchJson(path) {
  const response = await fetch(path, { cache: "no-store" });
  if (!response.ok) throw new Error(`${path} · HTTP ${response.status}`);
  return response.json();
}

async function boot() {
  try {
    const index = await fetchJson("data/index.json");
    DATA.index = index;
    DATA.boardMap = Object.fromEntries(index.boards.map((board) => [board.id, board]));

    const editions = await Promise.all(index.editions.map(async (meta) => ({
      ...meta,
      ...(await fetchJson(meta.file))
    })));
    DATA.editions = editions.sort((a, b) => b.date.localeCompare(a.date));

    const reports = await Promise.all(index.reports.map(async (meta) => ({
      ...meta,
      ...(await fetchJson(meta.file))
    })));
    DATA.reports = reports.sort((a, b) => b.month.localeCompare(a.month));

    bindGlobalEvents();
    updateFooter();
    renderRoute();
  } catch (error) {
    console.error(error);
    renderError(error);
  }
}

function updateFooter() {
  const year = new Date().getFullYear();
  $("#footerYear").textContent = String(year);
  const updated = DATA.index?.site?.updatedAt;
  if (updated) {
    const date = new Date(updated);
    const text = new Intl.DateTimeFormat("zh-CN", {
      year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Shanghai"
    }).format(date).replace(/\//g, "-");
    $("#footerUpdated").textContent = `数据更新于 ${text}（北京时间）`;
  }
}

/* ---------- routing ---------- */
function parseRoute() {
  const raw = (window.location.hash || "#/").replace(/^#/, "");
  const [pathPart, queryPart] = raw.split("?");
  const params = new URLSearchParams(queryPart || "");
  const segments = pathPart.split("/").filter(Boolean);
  const board = DATA.boardMap[params.get("board")] ? params.get("board") : "all";

  if (!segments.length) return { name: "week", id: null, board };
  switch (segments[0]) {
    case "week":
      return { name: "week", id: segments[1] || null, board };
    case "archive":
      return { name: "archive" };
    case "reports":
      return { name: "reports", id: segments[1] || null };
    case "search":
      return { name: "search", q: params.get("q") || "", board };
    case "about":
      return { name: "about" };
    default:
      return { name: "notfound" };
  }
}

function renderRoute() {
  const route = parseRoute();
  const routeKey = [route.name, route.id || "", route.q || "", route.board || ""].join("|");
  const sameKind = state.lastRouteKey.split("|")[0] === route.name;
  const shouldScroll = !sameKind || route.name !== "search";

  markNav(route.name);
  syncSearchInput(route);

  switch (route.name) {
    case "week":
      renderWeek(route);
      break;
    case "archive":
      renderArchive();
      break;
    case "reports":
      renderReports(route);
      break;
    case "search":
      renderSearch(route);
      break;
    case "about":
      renderAbout();
      break;
    default:
      renderNotFound();
  }

  state.lastRouteKey = routeKey;
  closeMobileNav();
  if (shouldScroll) window.scrollTo(0, 0);
}

function markNav(name) {
  const navName = name === "notfound" ? "" : name;
  $$(".main-nav a, .mobile-nav a").forEach((link) => {
    const active = link.dataset.nav === navName;
    link.classList.toggle("is-active", active);
    if (active) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  });
}

function syncSearchInput(route) {
  const input = $("#searchInput");
  if (!input) return;
  if (route.name === "search") {
    if (document.activeElement !== input && input.value !== route.q) input.value = route.q || "";
  } else if (document.activeElement !== input) {
    input.value = "";
  }
}

/* ---------- week view ---------- */
function renderWeek(route) {
  const edition = route.id
    ? DATA.editions.find((item) => item.id === route.id)
    : DATA.editions[0];

  if (!edition) {
    renderNotFound("这一期周报不存在，或还没有发布。");
    return;
  }

  const activeBoard = route.board || "all";
  const allItems = edition.items || [];
  const allBriefs = edition.briefs || [];
  const counts = countBoards(edition);
  const items = activeBoard === "all" ? allItems : allItems.filter((item) => item.board === activeBoard);
  const briefs = activeBoard === "all" ? allBriefs : allBriefs.filter((brief) => brief.board === activeBoard);
  const total = allItems.length + allBriefs.length;
  const covered = new Set([...allItems, ...allBriefs].map((entry) => entry.board)).size;
  const isCurrent = edition.id === DATA.editions[0]?.id;
  const next = nextUpdateParts();

  const hero = `
    <section class="week-hero" data-issue="${pad2(edition.issue)}">
      <div class="wrap hero-grid">
        <div class="hero-main">
          <div class="hero-meta">
            <span class="meta-item"><span class="live-dot"></span>${isCurrent ? "本周已更新" : "历史存档"}</span>
            <span class="meta-item">${fmtDate(edition.date)} · ${weekdayCN(edition.date)}</span>
            <span class="meta-item"><strong>${allItems.length}</strong> 条要闻${allBriefs.length ? ` · <strong>${allBriefs.length}</strong> 条简报` : ""}</span>
          </div>
          <p class="eyebrow">WEEKLY BRIEF / 第 ${edition.issue} 期</p>
          <h1>${esc(edition.title)}</h1>
          <p class="hero-intro">${esc(edition.intro)}</p>
        </div>
        <aside class="hero-side">
          <div>
            <div class="issue-label">ISSUE</div>
            <div class="issue-num">${pad2(edition.issue)}</div>
            <div class="issue-date">${fmtDate(edition.date)}</div>
            <div class="issue-week">${esc(edition.weekLabel || "")} · ${weekdayCN(edition.date)} ${publishTime(edition.publishedAt)}</div>
          </div>
          <div class="next-update">
            下次更新
            <strong>${esc(next.label)}</strong>
            采集窗口 ${fmtDateShort(edition.window.from)}–${fmtDateShort(edition.window.to)}
          </div>
        </aside>
      </div>
    </section>
  `;

  const toolbar = `
    <div class="week-toolbar">
      <div class="wrap toolbar-inner">
        ${boardChips(route, counts, activeBoard, total)}
        <div class="filter-note">${items.length} 条要闻${briefs.length ? ` · ${briefs.length} 条简报` : ""}</div>
      </div>
    </div>
  `;

  const groups = DATA.index.boards
    .map((board) => ({
      board,
      items: items.filter((item) => item.board === board.id),
    }))
    .filter((group) => group.items.length);

  let mainContent = groups
    .map((group, index) => renderBoardSection(group, edition, index, activeBoard))
    .join("");
  if (!items.length) {
    mainContent += `
      <div class="empty-note">
        本期「${esc(boardOf(activeBoard).name)}」暂无要闻${briefs.length ? "，以下为相关行业简报。" : "，可切换板块查看其他内容。"}
      </div>
    `;
  }
  if (briefs.length) mainContent += renderBriefs(briefs);

  const content = `
    ${hero}
    ${toolbar}
    <div class="wrap week-layout">
      <div class="week-main">${mainContent}</div>
      <aside class="week-side">${renderSidebar(edition, counts, covered, total)}</aside>
    </div>
  `;

  $("#app").innerHTML = content;
  updateDocumentTitle(`第 ${edition.issue} 期 · ${edition.title}`);
}

function boardChips(route, counts, activeBoard, total) {
  const editionId = route.id || DATA.editions[0]?.id;
  const base = `#/week/${editionId}`;
  const makeHref = (boardId) => {
    const params = new URLSearchParams();
    if (boardId !== "all") params.set("board", boardId);
    return `${base}${params.toString() ? `?${params}` : ""}`;
  };

  const allChip = `
    <a class="chip ${activeBoard === "all" ? "is-active" : ""}" href="${makeHref("all")}" ${activeBoard === "all" ? 'aria-current="true"' : ""}>
      全部 <span class="count">${total}</span>
    </a>
  `;

  const chips = DATA.index.boards.map((board) => {
    const count = counts[board.id] || 0;
    return `
      <a class="chip ${activeBoard === board.id ? "is-active" : ""}" href="${makeHref(board.id)}" ${activeBoard === board.id ? 'aria-current="true"' : ""}>
        ${boardDot(board.id)}${esc(board.short)} <span class="count">${count}</span>
      </a>
    `;
  }).join("");

  return `<div class="chips">${allChip}${chips}</div>`;
}

function renderBoardSection(group, edition, index, activeBoard) {
  const { board, items } = group;
  const editionId = edition.id;
  const backHref = `#/week/${editionId}`;
  const jump = activeBoard === "all"
    ? `<a class="board-jump" href="#/week/${editionId}?board=${board.id}">只看本板块 <span aria-hidden="true">→</span></a>`
    : `<a class="board-jump" href="${backHref}">查看全部板块 <span aria-hidden="true">→</span></a>`;
  return `
    <section class="board-section" id="board-${esc(board.id)}" style="--b:${esc(board.color)}">
      <div class="board-section-head">
        <div class="board-section-title">
          <span class="board-kicker">${pad2(index + 1)} / BOARD</span>
          <h2>${esc(board.name)}<span class="board-count">${items.length} 条</span></h2>
          <p>${esc(board.description || "")}</p>
        </div>
        ${jump}
      </div>
      <div class="item-grid board-items">
        ${items.map((item, itemIndex) => renderCard(item, edition, itemIndex + 1)).join("")}
      </div>
    </section>
  `;
}

function renderCard(item, edition, index) {
  const board = boardOf(item.board);
  return `
    <article class="news-card" id="item-${esc(item.id)}" style="--b:${esc(board.color)}; --i:${index}">
      <div class="card-top">
        ${boardTag(item.board)}
        <span class="importance ${esc(item.importance || "medium")}">${importanceLabel(item.importance)}</span>
        <span class="card-index">${pad2(index)}</span>
      </div>
      <h3 class="card-title">
        <a href="${safeUrl(item.sourceUrl)}" target="_blank" rel="noopener noreferrer">${esc(item.title)}</a>
      </h3>
      <p class="card-summary">${esc(item.summary)}</p>
      <div class="insight">
        <div class="insight-label">业务启发 <span>WHY IT MATTERS</span></div>
        <p class="insight-text">${esc(item.whyMatters)}</p>
      </div>
      <div class="insight next">
        <div class="insight-label">发展启示 <span>WHAT'S NEXT</span></div>
        <ul class="insight-list">${(item.implications || []).map((line) => `<li>${esc(line)}</li>`).join("")}</ul>
      </div>
      ${renderActions(item)}
      ${renderTags(item)}
      ${renderCardSource(item)}
    </article>
  `;
}

function renderActions(item) {
  if (!item.actions?.length) return "";
  return `<div class="actions-row">${item.actions.map((action) => `<span class="action-chip">${esc(action)}</span>`).join("")}</div>`;
}

function renderTags(item) {
  if (!item.tags?.length) return "";
  return `<div class="tag-row">${item.tags.map((tag) => `<span class="tag">#${esc(tag)}</span>`).join("")}</div>`;
}

function renderCardSource(item) {
  return `
    <footer class="card-source">
      <span class="source-name">${esc(item.source)}</span>
      <span class="source-date">${esc(item.publishedAt)}</span>
      ${item.verified ? `<span class="verified">${CHECK_ICON} 原文可核验</span>` : ""}
      <a class="source-link" href="${safeUrl(item.sourceUrl)}" target="_blank" rel="noopener noreferrer" aria-label="查看原文（新窗口打开）">查看原文 ↗</a>
    </footer>
  `;
}

function renderBriefs(briefs) {
  const rows = briefs.map((brief) => `
    <div class="brief-row" style="--b:${esc(boardOf(brief.board).color)}">
      ${boardTag(brief.board)}
      <div>
        <h3 class="brief-title">
          <a href="${safeUrl(brief.sourceUrl)}" target="_blank" rel="noopener noreferrer">${esc(brief.title)}</a>
        </h3>
        <p class="brief-summary">${esc(brief.summary)}</p>
      </div>
      <div class="brief-meta">
        <span class="source-name">${esc(brief.source)}</span>
        <span>${esc(brief.publishedAt)}</span>
        <a class="source-link" href="${safeUrl(brief.sourceUrl)}" target="_blank" rel="noopener noreferrer">原文 ↗</a>
      </div>
    </div>
  `).join("");

  return `
    <section class="briefs-section">
      <div class="section-head">
        <div>
          <p class="eyebrow">SIGNAL NOTES</p>
          <h2 class="section-title">行业简报</h2>
        </div>
        <span class="section-kicker">${briefs.length} 条 · 快速浏览</span>
      </div>
      ${rows}
    </section>
  `;
}

function renderSidebar(edition, counts, covered, total) {
  const boards = DATA.index.boards
    .map((board) => ({ ...board, count: counts[board.id] || 0 }))
    .filter((board) => board.count > 0);
  const max = Math.max(2, ...boards.map((board) => board.count));
  const next = nextUpdateParts();

  const distribution = boards.map((board) => `
    <div class="dist-row">
      <span class="dist-name">${esc(board.short)}</span>
      <span class="dist-track"><span class="dist-fill" style="--b:${esc(board.color)}; width:${Math.round((board.count / max) * 100)}%"></span></span>
      <span class="dist-count">${board.count}</span>
    </div>
  `).join("");

  const recent = DATA.editions.slice(0, 3).map((item) => `
    <a class="side-link" href="#/week/${item.id}">
      <time>${fmtDateShort(item.date)}</time>
      <span>${esc(item.title)}</span>
    </a>
  `).join("");

  return `
    <div class="side-stack">
      <section class="side-card">
        <div class="side-title"><strong>本期板块分布</strong><span>${covered} / 7</span></div>
        <div class="dist-list">${distribution || '<p class="side-note">本期暂无数据。</p>'}</div>
      </section>

      <section class="side-card dark">
        <div class="side-title"><strong>更新节奏</strong><span>CADENCE</span></div>
        <p class="side-dark-text">每周一 <strong>08:00</strong> 自动更新，每个板块精选 3–5 条，覆盖七大业务板块。</p>
        <div class="side-dark-date">${esc(next.date)}</div>
        <p class="side-dark-text">下一期 · 北京时间 ${esc(next.time)}</p>
        <div class="side-dark-meta"><span>采集窗口 7 天</span><span>共 ${total} 条内容</span></div>
      </section>

      <section class="side-card">
        <div class="side-title"><strong>近期存档</strong><span>ARCHIVE</span></div>
        <div class="side-links">${recent}</div>
      </section>

      <section class="side-card">
        <div class="side-title"><strong>关于情报站</strong><span>METHOD</span></div>
        <p class="side-note">每条新闻均可点击原文核验；业务启发基于公开信息与货代视角分析。详见 <a href="#/about">来源与方法</a>。</p>
      </section>
    </div>
  `;
}

/* ---------- archive ---------- */
function renderArchive() {
  const groups = {};
  DATA.editions.forEach((edition) => {
    const month = edition.date.slice(0, 7);
    if (!groups[month]) groups[month] = [];
    groups[month].push(edition);
  });

  const months = Object.keys(groups).sort().reverse();
  const body = months.map((month) => {
    const [year, monthNumber] = month.split("-");
    const rows = groups[month].map((edition, index) => {
      const boards = (edition.boards || []).slice(0, 7).map((id) => {
        const board = boardOf(id);
        return `<span class="mini-board">${boardDot(id)}${esc(board.short)}</span>`;
      }).join("");

      return `
        <a class="archive-row" href="#/week/${edition.id}" style="--i:${index}">
          <div class="archive-date">
            <strong>${fmtDateShort(edition.date)}</strong>
            <span>${esc(edition.weekLabel || "")} · ${weekdayCN(edition.date)}</span>
          </div>
          <div class="archive-body">
            <h3>${esc(edition.title)}</h3>
            <p>${esc(edition.intro)}</p>
            <div class="archive-boards">${boards}</div>
          </div>
          <div class="archive-count">
            <strong>${edition.itemCount}</strong>条要闻${edition.briefCount ? ` + ${edition.briefCount} 简报` : ""}
            <span class="archive-arrow">→</span>
          </div>
        </a>
      `;
    }).join("");

    return `
      <section class="archive-group">
        <div class="archive-month">${year} 年 ${Number(monthNumber)} 月</div>
        ${rows}
      </section>
    `;
  }).join("");

  $("#app").innerHTML = `
    <header class="page-head">
      <div class="wrap">
        <p class="eyebrow">ARCHIVE / 存档</p>
        <h1>历史存档</h1>
        <p class="page-lead">每一期周报都保留原始链接与业务解读。可以按时间回看，也可以直接搜索关键词，找到某个板块或某条判断的来龙去脉。</p>
      </div>
    </header>
    <div class="wrap page-body">
      ${body || renderEmptyBlock("还没有历史周报", "第一期周报发布后会出现在这里。")}
    </div>
  `;

  updateDocumentTitle("历史存档");
}

/* ---------- reports ---------- */
function renderReports(route) {
  const report = route.id
    ? DATA.reports.find((item) => item.id === route.id)
    : DATA.reports[0];

  if (!report) {
    $("#app").innerHTML = `
      <header class="page-head"><div class="wrap">
        <p class="eyebrow">MONTHLY BRIEF</p><h1>月度报告</h1>
        <p class="page-lead">每月 1 日，系统会把上月周报汇总成一份固定版式的形势分析。</p>
      </div></header>
      <div class="wrap page-body">${renderEmptyBlock("本期月报尚未生成", "月末最后一份周报发布后，月报会自动生成。")}</div>
    `;
    updateDocumentTitle("月度报告");
    return;
  }

  const signals = (report.signals || []).map((signal) => `
    <div class="signal-card" data-tone="${esc(signal.tone || "neutral")}">
      <div class="signal-value">${esc(signal.value)}<span class="unit">${esc(signal.unit || "")}</span></div>
      <div class="signal-label">${esc(signal.label)}</div>
      <div class="signal-note">${esc(signal.note || "")}</div>
    </div>
  `).join("");

  const judgements = (report.keyJudgements || []).map((judgement, index) => `
    <li><span class="num">0${index + 1}</span><span>${esc(judgement)}</span></li>
  `).join("");

  const boardReports = (report.boards || []).map((boardReport, index) => renderReportBoard(boardReport, index)).join("");

  const actions = (report.actions || []).map((action) => `
    <div class="action-row">
      <span class="action-horizon">${esc(action.horizon)}</span>
      <span class="action-text">${esc(action.action)}</span>
      <span class="action-owner">${esc(action.owner || "")}</span>
    </div>
  `).join("");

  const monthSwitcher = DATA.reports.length > 1 ? `
    <div class="month-switch">
      ${DATA.reports.map((item) => `<a class="${item.id === report.id ? "is-active" : ""}" href="#/reports/${item.id}">${item.month.replace("-", " 年 ")} 月</a>`).join("")}
    </div>
  ` : "";

  $("#app").innerHTML = `
    <div class="wrap">
      <section class="report-cover" data-month="${esc(report.month.slice(-2))}">
        <div class="cover-grid">
          <div class="cover-main">
            <p class="eyebrow light">MONTHLY BRIEF / 月度形势分析</p>
            <h1 class="cover-title">${esc(report.title)}</h1>
            <p class="cover-sub">${esc(report.subtitle)}</p>
            <div class="cover-meta">
              <span class="status-badge">${report.status === "month-to-date" ? "月中版 · 月末定稿" : "定稿版"}</span>
              <span>生成于 ${fmtDate(report.generatedAt)}</span>
              <span>覆盖 ${(report.boards || []).length} 个板块</span>
            </div>
          </div>
          <div class="cover-side">
            <p class="cover-note">${esc(report.coverNote || "")}</p>
            <div class="cover-actions">
              <button class="btn light" type="button" data-print>打印 / 存为 PDF</button>
            </div>
            ${monthSwitcher}
          </div>
        </div>
      </section>
    </div>

    <div class="wrap report-body">
      <section class="report-section">
        <div class="report-section-head"><h2>执行摘要</h2><span>EXECUTIVE SUMMARY</span></div>
        <div class="exec-summary">${esc(report.executiveSummary)}</div>
        ${judgements ? `<ul class="judgement-list">${judgements}</ul>` : ""}
      </section>

      <section class="report-section">
        <div class="report-section-head"><h2>关键信号</h2><span>KEY SIGNALS</span></div>
        <div class="signals-grid">${signals}</div>
      </section>

      <section class="report-section">
        <div class="report-section-head"><h2>分板块形势</h2><span>BY SECTOR</span></div>
        ${boardReports}
      </section>

      <section class="report-section">
        <div class="report-section-head"><h2>行动清单</h2><span>ACTION LIST</span></div>
        <div class="actions-table">${actions}</div>
      </section>

      <p class="method-note">${esc(report.method || "")}</p>
    </div>
  `;

  updateDocumentTitle(report.title);
}

function renderReportBoard(boardReport, index) {
  const board = boardOf(boardReport.board);
  const list = (items) => `<ul class="report-list">${(items || []).map((item) => `<li>${esc(item)}</li>`).join("")}</ul>`;
  const related = (boardReport.related || []).map((item) => `
    <div class="related-item">
      <time>${esc(item.date)}</time>
      <a href="${safeUrl(item.url)}" target="_blank" rel="noopener noreferrer">${esc(item.title)}</a>
      <span>· ${esc(item.source)}</span>
    </div>
  `).join("");

  return `
    <article class="board-report" style="--b:${esc(board.color)}; --i:${index}">
      <div class="board-report-head">
        ${boardTag(boardReport.board)}
        <span class="section-kicker">${esc(BOARD_EN[boardReport.board] || "")}</span>
      </div>
      <h3 class="board-report-title">${esc(boardReport.headline)}</h3>
      <p class="board-report-summary">${esc(boardReport.summary)}</p>
      <div class="board-report-grid">
        <div class="report-col">
          <h4>关键趋势<span class="cn">Trends</span></h4>
          ${list(boardReport.trends)}
        </div>
        <div class="report-col">
          <h4>对货代的影响<span class="cn">Implications</span></h4>
          ${list(boardReport.implications)}
        </div>
        <div class="report-col">
          <h4>下阶段关注<span class="cn">Watchlist</span></h4>
          ${list(boardReport.watch)}
        </div>
      </div>
      ${related ? `<div class="related-list">${related}</div>` : ""}
    </article>
  `;
}

/* ---------- search ---------- */
function renderSearch(route) {
  const query = (route.q || "").trim();
  const activeBoard = route.board || "all";

  if (!query) {
    const tagCounts = new Map();
    const addTags = (entry) => (entry.tags || []).forEach((tag) => tagCounts.set(tag, (tagCounts.get(tag) || 0) + 1));
    DATA.editions.forEach((edition) => {
      (edition.items || []).forEach(addTags);
      (edition.briefs || []).forEach(addTags);
    });
    const popular = Array.from(tagCounts.entries()).sort((a, b) => b[1] - a[1]).slice(0, 16);

    $("#app").innerHTML = `
      <section class="search-head">
        <div class="wrap">
          <p class="eyebrow">SEARCH / 检索</p>
          <h1 class="search-title">搜索全部存档</h1>
          <p class="page-lead">输入运价、航线、合规、项目货、PSC、理货等关键词，检索所有周报的标题、摘要与业务解读。</p>
          <div class="search-stats"><span>已收录 <strong>${DATA.editions.reduce((sum, edition) => sum + (edition.items || []).length + (edition.briefs || []).length, 0)}</strong> 条情报</span><span>覆盖 <strong>${DATA.editions.length}</strong> 期周报</span></div>
        </div>
      </section>
      <div class="wrap search-layout">
        <div class="side-card">
          <div class="side-title"><strong>热门关键词</strong><span>POPULAR</span></div>
          <div class="tag-cloud">
            ${popular.map(([tag]) => `<a class="popular-tag" href="#/search?q=${encodeURIComponent(tag)}">#${esc(tag)}</a>`).join("")}
          </div>
        </div>
        <div class="search-empty" style="margin-top:22px">
          <strong>从关键词开始</strong>
          也可以直接浏览最新一期，或到历史存档按时间回看。
          <div style="margin-top:16px"><a class="btn ghost" href="#/">阅读本周情报</a> <a class="btn ghost" href="#/archive">打开历史存档</a></div>
        </div>
      </div>
    `;
    updateDocumentTitle("搜索");
    return;
  }

  const results = [];
  DATA.editions.forEach((edition) => {
    const entries = [
      ...(edition.items || []).map((item) => ({ ...item, kind: "item", edition })),
      ...(edition.briefs || []).map((brief) => ({ ...brief, kind: "brief", edition }))
    ];
    entries.forEach((entry) => {
      const haystack = [
        entry.title,
        entry.summary,
        entry.whyMatters,
        ...(entry.implications || []),
        ...(entry.actions || []),
        ...(entry.tags || []),
        entry.source
      ].join(" ").toLowerCase();
      if (haystack.includes(query.toLowerCase())) results.push(entry);
    });
  });

  const counts = {};
  results.forEach((entry) => {
    counts[entry.board] = (counts[entry.board] || 0) + 1;
  });
  const filtered = activeBoard === "all" ? results : results.filter((entry) => entry.board === activeBoard);
  const editionsCovered = new Set(filtered.map((entry) => entry.edition.id)).size;
  const chips = searchChips(activeBoard, counts, results.length, query);

  const cards = filtered.map((entry, index) => {
    const board = boardOf(entry.board);
    const why = entry.whyMatters
      ? `<div class="result-insight">业务启发：${highlight(truncate(entry.whyMatters, 150), query)}</div>`
      : "";
    const tags = (entry.tags || []).slice(0, 5).map((tag) => `<span class="tag">#${esc(tag)}</span>`).join("");

    return `
      <article class="result-card" style="--b:${esc(board.color)}; --i:${index}">
        <div class="result-meta">
          ${boardTag(entry.board)}
          <a class="edition-link" href="#/week/${entry.edition.id}">第 ${entry.edition.issue} 期 · ${fmtDate(entry.edition.date)}</a>
          <span>${entry.kind === "brief" ? "行业简报" : "要闻"}</span>
          <span>${esc(entry.source)} · ${esc(entry.publishedAt)}</span>
        </div>
        <h2 class="result-title">
          <a href="${safeUrl(entry.sourceUrl)}" target="_blank" rel="noopener noreferrer">${highlight(entry.title, query)}</a>
        </h2>
        <p class="result-snippet">${highlight(truncate(entry.summary, 190), query)}</p>
        ${why}
        ${tags ? `<div class="result-tags">${tags}</div>` : ""}
      </article>
    `;
  }).join("");

  $("#app").innerHTML = `
    <section class="search-head">
      <div class="wrap">
        <p class="eyebrow">SEARCH / 检索</p>
        <h1 class="search-title">搜索 “<em>${esc(query)}</em>”</h1>
        <div class="search-stats">
          <span><strong>${filtered.length}</strong> 条结果</span>
          <span>覆盖 <strong>${editionsCovered}</strong> 期周报</span>
          <span>按时间倒序排列</span>
        </div>
        <div style="margin-top:18px">${chips}</div>
      </div>
    </section>
    <div class="wrap search-layout">
      ${filtered.length ? `<div class="result-list">${cards}</div>` : `
        <div class="search-empty">
          <strong>没有匹配的结果</strong>
          试试更短的关键词，或切换到其他板块。可以搜索“运价”“空运”“PSC”“理货”“项目货”等。
          <div style="margin-top:16px"><a class="btn ghost" href="#/search">清空搜索</a></div>
        </div>
      `}
    </div>
  `;

  updateDocumentTitle(`搜索：${query}`);
}

function searchChips(activeBoard, counts, total, query) {
  const hrefFor = (boardId) => {
    const params = new URLSearchParams();
    if (query) params.set("q", query);
    if (boardId !== "all") params.set("board", boardId);
    return `#/search${params.toString() ? `?${params}` : ""}`;
  };

  const all = `
    <a class="chip ${activeBoard === "all" ? "is-active" : ""}" href="${hrefFor("all")}" ${activeBoard === "all" ? 'aria-current="true"' : ""}>
      全部 <span class="count">${total}</span>
    </a>
  `;

  const boards = DATA.index.boards.map((board) => {
    const count = counts[board.id] || 0;
    if (!count) return "";
    return `
      <a class="chip ${activeBoard === board.id ? "is-active" : ""}" href="${hrefFor(board.id)}" ${activeBoard === board.id ? 'aria-current="true"' : ""}>
        ${boardDot(board.id)}${esc(board.short)} <span class="count">${count}</span>
      </a>
    `;
  }).join("");

  return `<div class="chips">${all}${boards}</div>`;
}

/* ---------- about ---------- */
function renderAbout() {
  const boards = DATA.index.boards.map((board) => `
    <div class="board-card" style="--b:${esc(board.color)}">
      <h3>${esc(board.name)}</h3>
      <p>${esc(board.description)}</p>
    </div>
  `).join("");

  const sources = [
    { name: "劳氏日报 Lloyd's List", type: "国际行业媒体", desc: "国际航运与海事的权威付费媒体", url: "https://www.lloydslist.com/" },
    { name: "航运界", type: "中文行业媒体", desc: "航运、港口与物流的中文资讯", url: "https://www.ship.sh/" },
    { name: "中国物流与采购网", type: "行业协会", desc: "中国物流与采购联合会官网", url: "http://www.chinawuliu.com.cn/" },
    { name: "国际船舶网", type: "行业媒体", desc: "船舶、航运与海工装备资讯", url: "https://www.eworldship.com/" },
    { name: "港口圈", type: "行业媒体", desc: "港口运营与港航动态（微信公众号）", url: "" },
    { name: "中国理货网", type: "行业协会", desc: "中国理货协会，理货行业动态与标准", url: "http://www.ctafair.org.cn/" },
    { name: "中国水运网", type: "官方行业媒体", desc: "交通运输部主管的水运行业媒体", url: "https://www.zgsyb.com/" },
    { name: "交通运输部", type: "政府主管部门", desc: "政策、监管、统计与行业会议", url: "https://www.mot.gov.cn/" },
    { name: "上海航运交易所", type: "运价指数机构", desc: "SCFI、CCFI 等运价指数与市场周报", url: "https://www.sse.net.cn/" },
    { name: "中国危化品物流网", type: "行业组织", desc: "化工与危化品物流行业资讯", url: "http://www.hcls.org.cn/" },
    { name: "中远海运", type: "航运与物流企业", desc: "船公司、港口与综合物流动态", url: "https://www.coscoshipping.com/" },
    { name: "国家统计局", type: "政府统计", desc: "宏观经济、价格与工业运行数据", url: "https://www.stats.gov.cn/" },
    { name: "SEKO Logistics", type: "国际货代", desc: "空运、海运与供应链市场更新", url: "https://www.sekologistics.com/zh/" },
    { name: "Breakbulk Asia", type: "项目物流展会", desc: "重大件与项目物流行业活动", url: "https://www.breakbulk.com/" },
    { name: "财联社", type: "财经媒体", desc: "快讯与市场数据", url: "https://www.cls.cn/" },
    { name: "澎湃新闻", type: "综合媒体", desc: "产业、物流与供应链报道", url: "https://www.thepaper.cn/" }
  ];

  const sourceRows = sources.map((source) => `
    <div class="source-row">
      ${source.url
        ? `<a href="${safeUrl(source.url)}" target="_blank" rel="noopener noreferrer">${esc(source.name)}</a>`
        : `<span class="source-name">${esc(source.name)}</span>`}
      <span class="source-type">${esc(source.type)}</span>
      <span class="source-desc">${esc(source.desc)}</span>
    </div>
  `).join("");

  $("#app").innerHTML = `
    <header class="page-head">
      <div class="wrap">
        <p class="eyebrow">ABOUT / 关于</p>
        <h1>来源与方法</h1>
        <p class="page-lead">一份可以被讨论的周报，前提是每条信息都能回到原文。这里说明我们如何选新闻、如何写启发，以及系统的更新方式。</p>
      </div>
    </header>

    <div class="wrap">
      <div class="about-intro">
        <div>
          <p class="eyebrow">OUR PRINCIPLE</p>
          <h1>先核验事实，<br>再谈判断。</h1>
          <p>航运物流的信息不缺，缺的是“与我有关”的判断。航策每周按 7 个板块分别筛选 3–5 条真正影响货代决策的新闻：运价、舱位、合规、客户与能力变化。每条都附原文链接，摘要只陈述来源支持的事实，业务启发明确标注为分析观点。</p>
        </div>
        <div class="about-stat">
          <div><strong>7</strong><span>业务板块</span></div>
          <div><strong>21–35</strong><span>每周要闻</span></div>
          <div><strong>100%</strong><span>附原文链接</span></div>
          <div><strong>0 元</strong><span>基础运行成本</span></div>
        </div>
      </div>

      <section class="about-section">
        <h2>我们如何保证新闻真实</h2>
        <p class="lead">真实性不是一句声明，而是一套可以检查的流程。</p>
        <div class="method-grid">
          <div class="method-card">
            <div class="method-num">01 / SOURCE</div>
            <h3>只选可追溯来源</h3>
            <p>优先使用行业媒体、政府主管部门、交易所、协会与企业官方发布；每条新闻保留来源名称、发布日期和原文链接。</p>
          </div>
          <div class="method-card">
            <div class="method-num">02 / VERIFY</div>
            <h3>关键数字回到原文</h3>
            <p>运价、吞吐量、同比增速等关键数字，以原文为准；无法核实的数字不写入摘要，宁可不写，也不猜测。</p>
          </div>
          <div class="method-card">
            <div class="method-num">03 / EDIT</div>
            <h3>事实与判断分开</h3>
            <p>摘要只呈现来源支持的事实；“业务启发”和“发展启示”是编辑分析，使用谨慎措辞，不把判断写成事实。</p>
          </div>
          <div class="method-card">
            <div class="method-num">04 / REVIEW</div>
            <h3>自动采集，人工可复核</h3>
            <p>自动化负责发现与整理，AI 仅用于摘要草稿；发布前可逐条打开原文复核。发现错误会直接修正并在存档中保留版本。</p>
          </div>
        </div>
      </section>

      <section class="about-section">
        <h2>七大业务板块</h2>
        <p class="lead">按货代的实际业务组织信息，而不是按媒体栏目。</p>
        <div class="board-grid">${boards}</div>
      </section>

      <section class="about-section">
        <h2>信息源</h2>
        <p class="lead">以下是重点监测的信息源。部分媒体存在付费墙，站内只引用可公开访问的标题、摘要与链接。</p>
        <div class="source-table">${sourceRows}</div>
      </section>

      <section class="about-section">
        <h2>业务启发如何生成</h2>
        <p class="lead">每条新闻都回答三个问题：对货代为什么重要？接下来可能发生什么？本周可以做什么？</p>
        <div class="callout">
          <div>
            <h3>一个判断，而不是一条快讯</h3>
            <p>我们把新闻放回货代的经营场景：运价与舱位如何影响报价，合规如何影响承运人选择，项目货如何影响代理网络。启发不追求“正确”，而追求“可讨论、可行动”。</p>
          </div>
          <a class="btn light" href="#/">阅读本周情报</a>
        </div>
      </section>

      <section class="about-section">
        <h2>技术与成本</h2>
        <p class="lead">为零预算团队设计：静态站点 + 免费自动化，不需要服务器和数据库。</p>
        <div class="tech-list">
          <div class="tech-item"><strong>前端</strong><span>无构建的 HTML / CSS / JavaScript，交互式单页，可直接托管在任意静态平台。</span></div>
          <div class="tech-item"><strong>数据</strong><span>周报与月报以 JSON 文件保存，纳入 Git 版本管理，搜索在浏览器本地完成。</span></div>
          <div class="tech-item"><strong>采集</strong><span>Python 3 脚本按板块关键词与可信域名筛选 RSS / 网页，自动去重并生成草稿。</span></div>
          <div class="tech-item"><strong>自动化</strong><span>GitHub Actions 每周一 08:00 更新周报，每月 1 日 10:00 汇总月报（北京时间）。</span></div>
          <div class="tech-item"><strong>托管</strong><span>Vercel / Netlify / GitHub Pages 免费层均可，推送代码即自动部署。</span></div>
          <div class="tech-item"><strong>成本</strong><span>基础运行 0 元；可选 GitHub Models 免费额度或自有 AI API Key 生成摘要草稿。</span></div>
        </div>
      </section>
    </div>
  `;

  updateDocumentTitle("来源与方法");
}

/* ---------- utility views ---------- */
function renderEmptyBlock(title, text) {
  return `<div class="search-empty"><strong>${esc(title)}</strong>${esc(text)}</div>`;
}

function renderNotFound(message) {
  $("#app").innerHTML = `
    <div class="error-panel">
      <p class="eyebrow">404 / NOT FOUND</p>
      <h1>没有找到内容</h1>
      <p>${esc(message || "页面可能已移动，或者链接不完整。")}</p>
      <a class="btn" href="#/">回到本周情报</a>
    </div>
  `;
  updateDocumentTitle("未找到页面");
}

function renderError(error) {
  const fileMode = window.location.protocol === "file:";
  $("#app").innerHTML = `
    <div class="error-panel">
      <p class="eyebrow">LOAD ERROR</p>
      <h1>数据没有载入</h1>
      <p>${fileMode
        ? "你正在直接用文件方式打开页面，浏览器会阻止读取 JSON 数据。请在本目录启动一个本地服务器："
        : "请检查 data/ 目录是否完整，以及网络连接是否正常。"}</p>
      ${fileMode ? "<code>python3 -m http.server 8080</code><p>然后访问 http://localhost:8080</p>" : ""}
      <p class="muted">${esc(error?.message || "未知错误")}</p>
    </div>
  `;
}

/* ---------- global events ---------- */
function bindGlobalEvents() {
  const input = $("#searchInput");
  const menuBtn = $("#menuBtn");
  const mobileNav = $("#mobileNav");

  input?.addEventListener("input", () => {
    window.clearTimeout(state.searchTimer);
    const query = input.value.trim();
    state.searchTimer = window.setTimeout(() => {
      if (!query) {
        if (parseRoute().name === "search") {
          history.replaceState(null, "", "#/search");
          renderRoute();
        }
        return;
      }
      history.replaceState(null, "", `#/search?q=${encodeURIComponent(query)}`);
      renderRoute();
    }, 220);
  });

  input?.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      input.value = "";
      input.blur();
      if (parseRoute().name === "search") window.location.hash = "#/";
    }
    if (event.key === "Enter") {
      event.preventDefault();
      window.clearTimeout(state.searchTimer);
      const query = input.value.trim();
      if (query) {
        history.replaceState(null, "", `#/search?q=${encodeURIComponent(query)}`);
        renderRoute();
      }
    }
  });

  document.addEventListener("keydown", (event) => {
    const tag = document.activeElement?.tagName || "";
    const typing = /INPUT|TEXTAREA|SELECT/.test(tag) || document.activeElement?.isContentEditable;
    if (event.key === "/" && !typing && !event.metaKey && !event.ctrlKey && !event.altKey) {
      event.preventDefault();
      input?.focus();
      input?.select();
    }
  });

  menuBtn?.addEventListener("click", () => {
    const open = menuBtn.getAttribute("aria-expanded") === "true";
    menuBtn.setAttribute("aria-expanded", String(!open));
    mobileNav.hidden = open;
  });

  mobileNav?.addEventListener("click", (event) => {
    if (event.target.closest("a")) closeMobileNav();
  });

  window.addEventListener("resize", () => {
    if (window.innerWidth > 980) closeMobileNav();
  });

  window.addEventListener("scroll", () => {
    $("#siteHeader")?.classList.toggle("is-scrolled", window.scrollY > 8);
    const toTop = $("#toTop");
    if (toTop) toTop.hidden = window.scrollY < 600;
  }, { passive: true });

  $("#toTop")?.addEventListener("click", () => {
    window.scrollTo({ top: 0, behavior: "smooth" });
  });

  document.addEventListener("click", (event) => {
    const printButton = event.target.closest("[data-print]");
    if (printButton) {
      event.preventDefault();
      window.print();
    }
  });

  window.addEventListener("hashchange", renderRoute);

  if (window.location.protocol === "file:") {
    renderError(new Error("file:// 模式无法读取 data/index.json"));
  }
}

function closeMobileNav() {
  const menuBtn = $("#menuBtn");
  const mobileNav = $("#mobileNav");
  if (!menuBtn || !mobileNav) return;
  menuBtn.setAttribute("aria-expanded", "false");
  mobileNav.hidden = true;
}

/* ---------- start ---------- */
boot();
