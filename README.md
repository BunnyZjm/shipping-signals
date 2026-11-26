# 航策 · SHIPPING SIGNALS

面向货代与航运物流团队的中文情报站：每周一早上 8 点发布周报，按 7 个业务板块组织，每个板块精选 3–5 条（总数 21–35 条）；每条都附原文链接、业务启发与发展启示；支持历史存档、搜索和每月固定版式的形势分析报告。

当前是一个**可直接打开、零构建、零服务器**的静态站点。数据和页面分离，自动化采集与写作由 GitHub Actions 定时执行，站点可以免费部署在 GitHub Pages、Vercel 或 Netlify。

线上地址：<https://bunnyzjm.github.io/shipping-signals/>

## 已实现

- **7 个业务板块**：集装箱物流、化工物流、合同物流、工程物流、航空物流、检验检测、理货
- **每板块 3–5 条**：单期 21–35 条，页面按板块分区展示；素材不足的板块可以少于 3 条，但不拿不相关新闻凑数
- **每周要闻**：每条包含摘要、原文链接、业务启发、发展启示、可执行动作、标签与重要度
- **交互式页面**：本周情报、板块筛选、历史存档、全文搜索（新闻 / 启发 / 启示）、月度报告、来源与方法
- **月度报告**：按板块汇总走势、影响、关注点与行动清单，支持打印 / 另存 PDF
- **自动化**：每周一 08:00（北京时间）采集与成稿；每月 1 日 10:00 生成上月定稿报告
- **事实核验**：采集器会抓取原文并记录状态；未拿到原文的候选会标记为未核验，不会伪装成“已核验”
- **人工兜底**：遇到反爬或付费墙，可在 `automation/inbox.json` 手动补链接，再走同一条流水线
- **发布把关**：`validate_data.py --strict` 校验条数、板块覆盖、字段、链接格式和占位文本，不合格不提交

## 目录结构

```
.
├── index.html                  # 单页入口
├── assets/
│   ├── app.js                  # 哈希路由、渲染、搜索、筛选
│   └── styles.css              # 编辑式视觉系统与响应式、打印样式
├── data/
│   ├── index.json              # 站点元数据、板块、周报/月报索引（脚本生成）
│   ├── editions/               # 每周数据，如 2026-11-24.json
│   └── reports/                # 月度报告，如 2026-11.json
├── config/
│   └── sources.json            # 新闻源、板块关键词、采集参数
├── scripts/
│   ├── lib.py                  # 抓取、解析、日期、索引、校验
│   ├── collect.py              # 采集候选 + 抓取原文
│   ├── compose.py              # AI 选稿与成稿（或离线 JSON 成稿）
│   ├── generate_report.py      # 月度报告汇总（可选 AI 润色）
│   ├── build_index.py          # 重建 data/index.json
│   └── validate_data.py        # 数据校验
├── automation/
│   ├── inbox.json              # 手动收录链接（可编辑）
│   ├── inbox.example.json      # 格式示例
│   ├── candidates.json         # 采集结果（本地生成，git 忽略）
│   ├── drafts/                 # 未通过校验的草稿（git 忽略）
│   └── articles/               # 原文摘要缓存（git 忽略）
├── .github/workflows/
│   ├── weekly.yml              # 每周一 08:00 采集→成稿→校验→提交→部署
│   ├── monthly.yml             # 每月 1 日 10:00 生成上月报告→提交→部署
│   └── deploy.yml              # 手动推送后的 Pages 部署
├── netlify.toml / vercel.json  # 免费托管配置
└── .nojekyll
```

## 新闻源

来源都在 `config/sources.json`，分为 RSS/Atom（`feeds`）与列表页（`pages`）两类：

- **国际航运与物流**：Lloyd's List、gCaptain、Splash 247、The Loadstar、Container News、FreightWaves、Offshore Energy、Heavy Lift & Project Forwarding、STAT Times、Air Cargo News、Port Strategy；
- **船公司与口岸**：马士基新闻中心、上海航运交易所、港口圈、中国海事局；
- **中文行业与政策**：航运界网、国际船舶网、中国物流与采购网（资讯 / 政策）、交通运输部、中国危化品物流网；
- **财经媒体**：第一财经（启用）；每日经济新闻（默认关闭，避免综合财经首页噪声）；
- **需要手动收录**：中国理货协会、中国国际货运代理协会、中远海运、达飞、地中海航运。这些站点存在反爬、付费墙或前端渲染，通用抓取拿不到文章列表，配置中保留条目并写明原因，重点公告请放进 `automation/inbox.json`。

`requireKeywords` 用于约束宽口径页面（如财经首页、船公司新闻中心）：标题或摘要必须命中其中一个词，才进入候选。某个来源连续抓取失败时，把它的 `enabled` 改为 `false`，不影响其他来源。

## 本地预览

不需要 Node.js，也不需要安装任何 Python 包：

```bash
cd 项目目录
python3 -m http.server 8080
```

打开 <http://127.0.0.1:8080/>。直接双击 `index.html` 不行，因为浏览器会拦截本地 `fetch` 读取 JSON。

## 内容模型

### 周报

文件：`data/editions/YYYY-MM-DD.json`

```json
{
  "id": "2026-11-24",
  "issue": 48,
  "date": "2026-11-24",
  "publishedAt": "2026-11-24T09:00:00+08:00",
  "window": { "from": "2026-11-18", "to": "2026-11-24" },
  "title": "本周主题",
  "intro": "本周主线概述",
  "items": [
    {
      "id": "c48-container-1",
      "board": "container",
      "title": "新闻标题",
      "source": "来源名称",
      "sourceUrl": "https://原文链接",
      "publishedAt": "2026-11-21",
      "summary": "120–220 字事实摘要",
      "whyMatters": "80–160 字业务启发",
      "implications": ["发展启示 1", "发展启示 2"],
      "actions": ["可执行动作 1", "可执行动作 2"],
      "tags": ["运价", "美线"],
      "importance": "high",
      "verified": true
    }
  ],
  "briefs": [
    {
      "id": "b48-chemical",
      "board": "chemical",
      "title": "简报标题",
      "source": "来源",
      "sourceUrl": "https://原文链接",
      "publishedAt": "2026-11-18",
      "summary": "60–120 字事实简报"
    }
  ]
}
```

板块 id：`container`、`chemical`、`contract`、`engineering`、`air`、`inspection`、`tally`。

条数规则（`config/sources.json` 的 `settings`）：

- `weeklyMinItems` / `weeklyMaxItems`：单期总数 21–35 条；
- `weeklyMaxPerBoard`：单板块最多 5 条；
- `weeklyTargetPerBoard`：每板块目标 3 条，素材充足时向 5 条扩展；
- `weeklyMinBoards`：至少覆盖 6 个板块；
- `perBoardSince`：从该日期起启用“每板块 3–5 条”校验，之前的旧期仍按旧的 3–5 条总数校验，避免改写历史。

`data/editions/2026-11-24.json` 是按新规则人工编排的 29 条样例（5/3/3/5/5/5/3），用于展示真实的多条密度；AI 自动生成从下一期开始接管。

写完后运行：

```bash
python3 scripts/build_index.py
python3 scripts/validate_data.py --strict
```

`data/index.json` 的标题、条数、板块、下次更新时间都是脚本推导出来的，不要手改这些字段。

### 手动新增一期

1. 复制最近一期 `data/editions/*.json` 改成新的日期；
2. 修改 `id`、`issue`、`date`、`publishedAt`、`window` 和正文；
3. 运行 `build_index.py` 与 `validate_data.py --strict`；
4. 提交并推送，Pages / Vercel / Netlify 会自动更新。

### 手动收录链接（收件箱）

中国理货协会等站点会对非浏览器请求返回 `Request Rejected`，Lloyd's List 等可能受付费墙限制。将链接加入 `automation/inbox.json`：

```json
{
  "items": [
    {
      "title": "新闻标题",
      "url": "https://原文链接",
      "board": "tally",
      "publishedAt": "2026-11-20",
      "source": "中国理货网",
      "summary": "可选，补充你知道的事实",
      "force": false
    }
  ]
}
```

`force: true` 表示即使早于回看窗口也保留。收件箱条目会和其他候选一起抓原文、去重、参与 AI 选稿。

## 自动化：每周一 08:00 / 每月 1 日 10:00

### 1. 上传到 GitHub

新建一个仓库，把整个项目推上去（公开或私有都可以）。如果还不会用命令，可以在 GitHub 网页选择 **Add file → Upload files**，把项目文件拖进去。

### 2. 打开写权限与 Pages

- **Settings → Actions → General → Workflow permissions**：选择 **Read and write permissions**。
- **Settings → Pages → Build and deployment → Source**：选择 **GitHub Actions**。
- 如果使用私有仓库，确认账户仍有 Actions 免费额度。

### 3. 配置 AI（必填）

> 2026-07-30 起 GitHub Models 的推理接口已下线（`models.github.ai` 只回一个 `OK`），所以改用 **OpenRouter 免费模型**。

1. 打开 [openrouter.ai](https://openrouter.ai/)，用 GitHub 账号登录；
2. `Keys` → `Create Key`，名字随意（如 `shipping-signals`），创建后立即复制 `sk-or-v1-...`；
3. 打开仓库 **Settings → Secrets and variables → Actions → Secrets → New repository secret**，Name 填 `AI_API_KEY`，Secret 粘贴刚才的 key，保存；
4. 如果免费模型提示需要授权，到 OpenRouter 的 **Settings → Privacy** 打开允许免费端点的开关（免费模型会记录请求内容用于改进模型，这里送的都是公开新闻，不涉及隐私）。

默认配置：

- 服务地址：`https://openrouter.ai/api/v1`
- 模型链：`inclusionai/ling-3.2-flash:free` → `baidu/ernie-5.5-flash-preview:free` → `google/gemma-4-31b-it:free` → `google/gemma-4-26b-a4b-it:free` → `openrouter/free`，前一个不可用自动换下一个；
- 想换模型：在 **Variables** 里设置 `AI_MODEL`（多个用英文逗号分隔），`AI_BASE_URL` 只在换服务商时才需要改。

免费额度约为每分钟 20 次、每天 50 次请求。脚本按板块分批调用（每板块 1 次，必要时补选 1 次），一期通常 8–12 次，留有余量。模型全部失败或成稿不足 21 条时，本次任务失败并保留 `automation/drafts/` 草稿 artifact，**不会发布未通过校验的内容**；重跑一次通常就能拿到结果。

### 4. 先手动跑一次

进入 **Actions → Weekly edition → Run workflow**。成功后会：

1. 采集 RSS 与站点列表页；
2. 抓取候选原文，记录是否核验；
3. 让 AI 按严格 JSON 结构为每个板块写出 3–5 条要闻（总数 21–35 条）；
4. 校验条数、板块、链接和字段；
5. 提交 `data/editions/` 与 `data/index.json`；
6. 部署 GitHub Pages。

第一次运行建议打开 artifact 里的 `automation/candidates.json` 和 `automation/drafts/`，检查选稿与摘要是否符合你的业务口味。

### 5. 定时说明

- 每周：`0 0 * * 1`（UTC）= 周一 08:00（北京时间）
- 每月：`0 2 1 * *`（UTC）= 每月 1 日 10:00（北京时间）
- GitHub 的定时任务可能延迟几分钟到几十分钟，但仍在当天上午完成。
- 定时任务使用默认分支；如果默认分支不是 `main`，请同步修改 `deploy.yml` 的 `branches`。

### 6. 本地演练（不调用 AI）

```bash
python3 scripts/collect.py --date 2026-11-30
python3 scripts/compose.py --date 2026-11-30 --from-json my-response.json --dry-run
python3 scripts/generate_report.py --month 2026-11 --status final --out /tmp/report.json
python3 scripts/validate_data.py --strict
```

`my-response.json` 只包含 `title`、`intro` 和 `items`（每项带 `candidateId`），结构与 `scripts/compose.py` 中的 `ITEM_SCHEMA` 一致。这样可以在不消耗 AI 额度的情况下测试排版、字段与校验流程。

## 月度报告

每月 1 日 10:00，工作流会：

1. 读取上个月全部周报；
2. 按板块汇总标题、事实摘要、走势判断、影响与关注点；
3. 生成 `data/reports/YYYY-MM.json`；
4. 可选调用 AI 润色表述（禁止新增事实和数字，失败自动回退）；
5. 校验并部署。

也可以手动生成任意月份：

```bash
python3 scripts/generate_report.py --month 2026-11 --status final
python3 scripts/build_index.py
python3 scripts/validate_data.py --strict
```

> 当前仓库里的 `2026-11` 报告是**月中版**，副标题会标注“月中版 · 基于第 47–48 周周报”。12 月 1 日工作流会基于 11 月全部周报重新生成定稿版。

## 部署到 Vercel / Netlify

两种方式都**不需要构建命令**：

- **Vercel**：Import 仓库 → Framework Preset 选 **Other** → Build Command 留空 → Output Directory 留空（根目录）→ Deploy。
- **Netlify**：Add new site → Import an existing project → Build command 留空 → Publish directory 填 `.`。

`netlify.toml` 与 `vercel.json` 已配置缓存和基础安全头。因为使用哈希路由（`#/archive`），不需要服务端重写规则。

GitHub Actions 提交后，托管平台通常会自动重新部署；如果平台没有触发，在平台控制台点一次 **Redeploy** 即可。

## 成本

在现有 Qoder 订阅之外，默认方案可以保持 **0 元**：

- GitHub Actions：公开仓库免费；私有仓库每月有免费分钟数（以 GitHub 当前政策为准）。本任务通常 2–5 分钟。
- GitHub Pages / Vercel / Netlify：免费套餐足够这个纯静态站点。
- 数据库、服务器、邮件服务：不需要。
- AI：使用 OpenRouter 的 `:free` 免费模型；额度用尽时任务停止，不会产生自动扣费。不要在工作流里绑定付费 API 而不设预算。

## 为什么没有用 Vue/React + Serverless + 云数据库

你最初提到 Vue/React、Serverless 和免费云数据库。这个项目刻意改成了**原生 HTML/CSS/ES Modules + 静态 JSON**，原因是：

1. 当前环境没有 Node.js，装一套构建链会把你锁在本地调试里；
2. 网站只有“读数据 + 筛选 + 搜索”，不需要服务端；
3. 零构建意味着 GitHub Pages、Vercel、Netlify 都能直接托管，迁移成本几乎为零；
4. JSON 数据可以被人和 AI 同时读写，出问题能直接看 diff。

交互、存档、搜索、月报，这些功能都已经实现。以后如果要接入登录、团队协作或数据库，再迁移到 Next.js + Supabase 也不迟，数据格式可以直接复用。

## 事实、版权与使用边界

- 摘要只陈述原文支持的事实，业务启发与发展启示明确标注为分析；
- 每条内容保留来源名称、发布日期和原文链接；
- 采集器只保存有限的原文片段用于写作，不转载全文；请遵守各来源的版权与付费墙规则；
- 关键数字以原文为准；AI 被明确要求不得补充材料外的事实或数字；
- `verified: true` 表示采集时成功抓取到原文；`false` 表示只有列表/摘要信息，界面不会显示“原文已核验”；
- 本站内容用于内部研判，不构成商业决策依据。

## 常见问题

**周报任务失败，提示“材料不足”？**  
检查 `automation/candidates.json` 的 `stats` 与 `warnings`。可能是某个来源改版、被反爬，或回看窗口内新闻太少。可以在 `inbox.json` 手动补 1–2 条，或临时调大 `--lookback`。

**某个来源总是 403 / 502？**  
把 `config/sources.json` 中对应来源的 `enabled` 改为 `false`，或改用 `inbox.json` 手动收录。中国理货协会目前默认关闭，原因已写在配置的 `note` 里。

**AI 返回的 JSON 不合法？**  
`compose.py` 会先写草稿再校验；失败时草稿保留在 artifact 中。可以换模型、降低候选数量，或用 `--from-json` 人工修正后重跑。

**提交被拒绝（protected branch）？**  
默认分支若开启保护，需要允许 GitHub Actions 推送，或改为由工作流创建 Pull Request。当前工作流按“直接推送默认分支”设计。

**页面数据没更新？**  
静态站点有缓存。GitHub Pages 通常 1–2 分钟内生效；Vercel/Netlify 检查最近一次部署；浏览器强制刷新。

**能加邮件推送吗？**  
可以，但会引入第三方邮件服务与密钥管理。当前按你的要求只做网页展示，避免额外成本和维护。
