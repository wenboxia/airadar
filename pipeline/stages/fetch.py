"""Stage 1: fetch —— 从信源注册表抓取原始条目。

这一层就是 agent 的 Tool Use：LLM 不联网，联网由这里的确定性代码完成，
从而保证信源可控（白名单）、内容可溯源（存原文）、摘要可评测（有原文才能查幻觉）。
"""
import hashlib
import os
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import feedparser
import requests
import yaml
from bs4 import BeautifulSoup

from ..config import ROOT
from ..guards import parallel_map
from ..models import Context, Item

MANIFEST = {
    "name": "fetch", "version": "0.1.0",
    "input": "sources.yaml", "output": "list[Item]（含原文文本）",
    "eval_cases": "evals/golden_set/",
}

UA = {"User-Agent": "AIRadar/0.1 (personal research radar; +https://github.com)"}
SOURCES_PATH = os.path.join(ROOT, "pipeline", "sources.yaml")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _to_iso(struct) -> str:
    if not struct:
        return ""
    return datetime(*struct[:6], tzinfo=timezone.utc).isoformat(timespec="seconds")


def parse_iso(s: str):
    """容错解析 ISO 时间。Python 3.10 的 fromisoformat 不认 'Z' 后缀（3.11 才支持），
    而 GitHub API 返回的正是 Z 格式——不处理会直接崩。"""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def norm_iso(s: str) -> str:
    """统一成 +00:00 形式。CLAUDE.md 约定所有时间存 UTC ISO，格式必须一致，
    否则 SQLite 的字符串比较（week 视图的时间窗查询）会出现难查的边界错误。"""
    d = parse_iso(s)
    return d.astimezone(timezone.utc).isoformat(timespec="seconds") if d else ""


def _soup_main(html: str):
    soup = BeautifulSoup(html or "", "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()
    return soup.find("article") or soup.find("main") or soup


def _clean_text(html: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", _soup_main(html).get_text(" ")).strip()
    return text[:limit]


# 正文里的外链是"谁在讨论哪篇论文"的唯一证据。get_text() 把 href 全剥了，
# 所以 D36 说的"被别处提到才抓论文"一直没有输入信号，交叉引用率也测不准（D36 认领的错误 1）
_ARXIV_RE = re.compile(r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})", re.I)


def outbound_links(html: str, text: str = "", limit: int = 40) -> dict:
    """抽外链。返回 {"arxiv": [论文 id], "links": [外链]}。

    纯文本里写的 URL 也算——量子位这类中文源常直接把链接打在正文里。
    """
    ids, links = [], []
    for a in _soup_main(html).find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith("http"):
            links.append(href)
    for blob in (html or "", text or ""):
        ids += _ARXIV_RE.findall(blob)
    seen = set()
    links = [u for u in links if not (u in seen or seen.add(u))][:limit]
    return {"arxiv": sorted(set(ids)), "links": links}


def _clean_markdown(md: str, limit: int) -> str:
    """Jina Reader 返回 markdown，导航链接要剥掉，否则正文被链接噪声淹没。"""
    body = md.split("Markdown Content:", 1)[-1]
    body = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", body)   # [文字](链接) → 文字
    body = re.sub(r"^\s*[*\-]\s.*$", "", body, flags=re.M)  # 导航项列表
    body = re.sub(r"[#>`|]", " ", body)
    return re.sub(r"\s+", " ", body).strip()[:limit]


def _fetch_direct(url: str, limit: int, item: Item = None) -> str:
    resp = requests.get(url, headers=UA, timeout=20)
    resp.raise_for_status()
    html = resp.text[:1_000_000]
    if item is not None:
        out = outbound_links(html)
        if out["arxiv"] or out["links"]:
            item.extra["outbound"] = out     # 全文比 RSS 摘要多得多，覆盖掉摘要里的
    return _clean_text(html, limit)


def _fetch_via_reader(url: str, limit: int) -> str:
    """Jina Reader 兜底：openai.com 等站点用 Cloudflare 屏蔽一切非浏览器请求（实测 403，
    换浏览器 UA 也没用），只能靠第三方提取服务。见 decisions.md D13。"""
    resp = requests.get(f"https://r.jina.ai/{url}", headers=UA, timeout=45)
    resp.raise_for_status()
    text = resp.text
    if "Target URL returned error" in text[:600]:
        raise requests.HTTPError("reader 回报目标 404")
    return _clean_markdown(text, limit)


def fetch_content(url: str, limit: int, item: Item) -> str:
    """内容获取降级链：直接抓取 → Jina Reader → 空（调用方保留 RSS 摘要）。

    与 LLM 降级链同构：每层失败都留痕，全失败也不阻塞。
    """
    for level, fn in (("direct", _fetch_direct), ("reader", _fetch_via_reader)):
        try:
            text = fn(url, limit, item) if fn is _fetch_direct else fn(url, limit)
            if len(text) >= 400:
                item.extra["content_source"] = level
                return text
        except Exception as ex:  # noqa: BLE001 逐层降级
            item.notes.append(f"fetch_{level}_failed: {type(ex).__name__}")
    return ""


def _make_item(url: str, title: str, src: dict, published: str,
               content: str) -> Item:
    return Item(
        url=url, title=title.strip(),
        source=src["name"], tier=src.get("tier", "D"),
        published_at=published, fetched_at=_now_iso(),
        content=content, topics=[],
    )


def _fetch_rss(src: dict, since: datetime, ctx: Context) -> list:
    resp = requests.get(src["url"], headers=UA, timeout=25)
    resp.raise_for_status()
    feed = feedparser.parse(resp.content)
    items = []
    for e in feed.entries[:30]:
        published = _to_iso(getattr(e, "published_parsed", None)
                            or getattr(e, "updated_parsed", None))
        # 无日期的条目保留（部分 feed 不带日期），有日期的按时间窗过滤
        pub_dt = parse_iso(published)
        if pub_dt and pub_dt < since:
            continue
        raw = ""
        if getattr(e, "content", None):
            raw = e.content[0].get("value", "")
        raw = raw or getattr(e, "summary", "")
        content = _clean_text(raw, ctx.cfg.content_max_chars)
        it = _make_item(e.link, e.title, src, published, content)
        it.extra["content_source"] = "rss"
        # 一半信源的 feed 自带官方分类标签（OpenAI 86%、Anthropic 100%），以前一条没读（D39）。
        # 不拿它替代模型分类——各家体系不同，量子位的标签其实是网站栏目名——只留作评测的参照
        tags = [t.get("term") for t in getattr(e, "tags", []) if t.get("term")]
        if tags:
            it.extra["source_tags"] = tags[:5]
        out = outbound_links(raw, content)
        if out["arxiv"] or out["links"]:
            it.extra["outbound"] = out
        items.append(it)

    # feed 里只有短摘要的条目去抓全文；并发执行（网络等待占绝大部分时间）
    thin = [it for it in items if len(it.content) < 500]
    if thin:
        def _enrich(it):
            full = fetch_content(it.url, ctx.cfg.content_max_chars, it)
            if full:
                it.content = full
        parallel_map(_enrich, thin, workers=6)
    return items


def _fetch_arxiv(src: dict, since: datetime, ctx: Context) -> list:
    n = src.get("max_results", 15)
    url = ("http://export.arxiv.org/api/query?search_query="
           f"{quote(src['query'])}&sortBy=submittedDate&sortOrder=descending"
           f"&max_results={n}")
    resp = requests.get(url, headers=UA, timeout=30)
    resp.raise_for_status()
    feed = feedparser.parse(resp.content)
    items = []
    # arXiv 按批次放榜，窗口放宽到 since 再往前 2 天
    arxiv_since = since - timedelta(days=2)
    for e in feed.entries:
        published = _to_iso(getattr(e, "published_parsed", None))
        pub_dt = parse_iso(published)
        if pub_dt and pub_dt < arxiv_since:
            continue
        abstract = re.sub(r"\s+", " ", getattr(e, "summary", "")).strip()
        it = _make_item(e.link, e.title, src, published, abstract)
        authors = [a.get("name", "") for a in getattr(e, "authors", [])][:6]
        it.extra["authors"] = authors
        items.append(it)
    return items


def title_words(title: str) -> set:
    """标题分词，附带去掉复数 s 的形式。

    整词匹配原本会漏掉复数——实测近 30 天 HN 上《Introducing System One Models》1301 分、
    《LLMs as a Cognitive Virus》394 分都因为 models≠model、llms≠llm 被丢掉。
    只补不替：原词仍保留，所以 "gpus" 同时产出 gpus 和 gpu，不会漏匹配原词。"""
    words = re.findall(r"[a-z0-9]+", title.lower())
    return set(words) | {w[:-1] for w in words if len(w) > 3 and w.endswith("s")}


def _fetch_hn(src: dict, since: datetime, ctx: Context) -> list:
    since_ts = int(since.timestamp())
    url = ("https://hn.algolia.com/api/v1/search?tags=story"
           f"&numericFilters=points>{src.get('min_points', 100)},created_at_i>{since_ts}"
           "&hitsPerPage=40")
    resp = requests.get(url, headers=UA, timeout=25)
    resp.raise_for_status()
    kws = [k.lower() for k in src.get("keywords", [])]
    items = []
    for h in resp.json().get("hits", []):
        title = h.get("title") or ""
        if kws and not (title_words(title) & set(kws)):
            continue
        link = h.get("url") or f"https://news.ycombinator.com/item?id={h['objectID']}"
        published = datetime.fromtimestamp(
            h["created_at_i"], tz=timezone.utc).isoformat(timespec="seconds")
        it = _make_item(link, title, src, published, "")
        it.extra["hn_points"] = h.get("points", 0)
        it.extra["hn_comments"] = h.get("num_comments", 0)
        items.append(it)
    parallel_map(
        lambda it: setattr(it, "content",
                           fetch_content(it.url, ctx.cfg.content_max_chars, it)),
        items, workers=6)
    return items


def _fetch_github(src: dict, since: datetime, ctx: Context) -> list:
    items = []
    created = (datetime.now(timezone.utc) - timedelta(days=30)).date().isoformat()
    for topic in src.get("topics_query", ["llm"]):
        url = ("https://api.github.com/search/repositories?"
               f"q=topic:{topic}+created:>{created}&sort=stars&order=desc"
               f"&per_page={src.get('per_topic', 5)}")
        resp = requests.get(url, headers={**UA, "Accept": "application/vnd.github+json"},
                            timeout=25)
        resp.raise_for_status()
        for repo in resp.json().get("items", []):
            it = _make_item(
                repo["html_url"], f"{repo['full_name']} — {repo.get('description') or ''}",
                src, norm_iso(repo.get("created_at", "")),
                (repo.get("description") or ""))
            it.extra["stars"] = repo.get("stargazers_count", 0)
            it.extra["language"] = repo.get("language")
            items.append(it)
        time.sleep(1)  # GitHub 未认证限速 10 次/分钟
    return items


def fetch_arxiv_by_id(ids: list, src: dict) -> list:
    """按 ID 取论文。论文的入口从"按提交时间发现"改成"别处提到才抓"（D36）：
    错的不是 arXiv，是排序键——从每天几百篇里按时间取 15 篇等于随机抽样。

    注意只有 https 有效，http 返回空——之前测出 0 条就是栽在这里。
    """
    if not ids:
        return []
    url = ("https://export.arxiv.org/api/query?id_list="
           f"{','.join(ids[:30])}&max_results={min(len(ids), 30)}")
    resp = requests.get(url, headers=UA, timeout=30)
    resp.raise_for_status()
    out = []
    for e in feedparser.parse(resp.content).entries:
        link = getattr(e, "link", "")
        published = _to_iso(getattr(e, "published_parsed", None))
        abstract = re.sub(r"\s+", " ", getattr(e, "summary", "")).strip()
        it = _make_item(link, getattr(e, "title", ""), src, published, abstract)
        names = [a.get("name", "") for a in getattr(e, "authors", [])]
        it.extra["authors"] = names[:8]
        # 名单只存前 8 个，但人数要记全：「署名 25 人以上」这条判据曾因截断在线上永远不触发
        it.extra["author_count"] = len(names)
        it.extra["arxiv_via"] = "mention"
        out.append(it)
    return out


def _is_lab_report(item: Item) -> bool:
    """发布探测器（D36 入口 2）：披着论文外衣的产品发布。
    DeepSeek-V3、Qwen 这类先发 arXiv 晚发博客，System Card 的评测表也只在 arXiv 那份里。"""
    authors = item.extra.get("authors") or []
    title = (item.title or "").lower()
    if any(k in title for k in ("technical report", "system card", "model card")):
        return True
    # 几十人署名的本质是产品发布，不是一篇普通预印本
    if item.extra.get("author_count", len(authors)) >= 25:
        return True
    # 机构名必须是**整个署名条目**（"DeepSeek-AI"、"Kimi Team"），不能在人名里做子串匹配：
    # 那样 Kimia Nadjahi、Kimin Lee、Baichuan Huang 都会被当成实验室，单个信源提到就放行
    return any(_LAB_AUTHOR_RE.match(a.strip()) for a in authors)


_LAB_AUTHOR_RE = re.compile(
    r"^(?:deepseek|openai|qwen|anthropic|moonshot|kimi|google deepmind|meta|mistral|zhipu|baichuan)"
    r"(?:[\s-]*(?:ai|team|inc\.?))?$", re.I)


def collect_mentions(items: list, db, src: dict, ctx: Context) -> list:
    """从本次抓到的正文外链里，取出被提及的 arXiv 论文（D36 入口 1）。

    收紧方向而不是放宽：只认"非 arXiv 信源提到的"，且一次最多 N 篇——
    参考文献列表里每篇论文都会被提到，不加闸门等于把 arXiv 又接了回来。
    """
    mentioned = {}
    for it in items:
        out = it.extra.get("outbound") or {}
        for pid in out.get("arxiv", []):
            mentioned.setdefault(pid, []).append(it.source)
    if not mentioned:
        # 0 篇也要留记录：否则分不清"今天没人提论文"和"这个入口根本没跑"（D40）
        ctx.stats.setdefault("fetch", {})["arxiv_mentions"] = {
            "seen": 0, "fetched": 0, "kept": 0, "stale_reports": 0, "not_requested": 0}
        return []
    existing = db.existing_ids() if db else set()
    # 接口一次只取前 30 个。按字符串升序截断时，名额先给了参考文献里的老论文
    # （arXiv 编号就是年月），一篇长文引 30 多篇旧作，当天被两个信源讨论的新论文就进不来。
    # 所以先按被几个信源提到、再按新旧排
    ids = sorted(mentioned, key=lambda p: (len(set(mentioned[p])), p), reverse=True)
    papers = fetch_arxiv_by_id(ids, src)
    picked, stale = [], 0
    for p in papers:
        from . import dedupe
        if dedupe.item_id(p.url) in existing:
            continue
        who = sorted(set(mentioned.get(_ARXIV_RE.search(p.url).group(1), [])
                         if _ARXIV_RE.search(p.url) else []))
        p.extra["mentioned_by"] = who
        # 被两个以上独立信源提到，或是**新发布的**实验室技术报告，这两种才够格。
        # 技术报告那条只认 14 天内的（和时效内容过期同一个数）：它放行只要一个信源提到，
        # 本意是抓新发布；不限时间时，HN 一帖顺手引用的 20 个月前的 DeepSeek-R1 被当成新闻发了。
        # 老报告要进来，得和普通论文一样被两个以上信源提到——"今天还在被讨论"才是收录理由（D36）
        if len(who) >= 2:
            picked.append(p)
        elif _is_lab_report(p):
            if _age_days(p.published_at) <= ctx.cfg.short_ttl_days:
                picked.append(p)
            else:
                stale += 1
    # 多信源讨论的排在单信源放行的技术报告前面，名额不够时先保前者
    picked.sort(key=lambda p: len(p.extra["mentioned_by"]), reverse=True)
    picked = picked[: src.get("max_results", 5)]
    ctx.stats.setdefault("fetch", {})["arxiv_mentions"] = {
        "seen": len(mentioned), "fetched": len(papers), "kept": len(picked),
        "stale_reports": stale, "not_requested": max(0, len(mentioned) - 30)}
    return picked


def _age_days(iso: str) -> float:
    """发布日期缺失或解析不了时当作很老——收紧方向，宁可不放行"""
    dt = parse_iso(iso or "")
    if not dt:
        return float("inf")
    return (datetime.now(timezone.utc) - dt).total_seconds() / 86400


_FETCHERS = {"rss": _fetch_rss, "arxiv": _fetch_arxiv,
             "hn": _fetch_hn, "github": _fetch_github}


def run(items: list, ctx: Context) -> list:
    with open(SOURCES_PATH, encoding="utf-8") as f:
        sources = yaml.safe_load(f)["sources"]
    since = datetime.now(timezone.utc) - timedelta(days=ctx.cfg.since_days)
    # arxiv_mentions 不是"去哪里抓"，是跑完其他信源后才执行的闸门，所以不进这一轮
    todo = [s for s in sources if s.get("tier") != "X" and s.get("type") != "arxiv_mentions"]
    per_source = {}

    def _one(src):
        fetcher = _FETCHERS.get(src.get("type"))
        if not fetcher:
            ctx.note_error("fetch", f"未知信源类型 {src.get('type')} ({src['name']})")
            return []
        try:
            got = fetcher(src, since, ctx)
        except Exception as ex:  # noqa: BLE001 —— 单信源失败不阻塞全局（兜底原则）
            per_source[src["name"]] = f"failed: {type(ex).__name__}"
            ctx.note_error("fetch", f"{src['name']}: {ex}")
            return []
        # 限量是"每个信源最多 N 条"而非全局截断——否则调试时只能取到第一个信源，样本有偏
        if ctx.cfg.per_source_limit:
            got = got[: ctx.cfg.per_source_limit]
        per_source[src["name"]] = len(got)
        print(f"  fetch [{src['tier']}] {src['name']}: {len(got)} 条")
        return got

    # 信源之间互相独立，并发抓取（13 个信源串行要几分钟，绝大部分时间在等网络）
    results = parallel_map(_one, todo, workers=6,
                           on_error=lambda s, e: ctx.note_error("fetch", f"{s['name']}: {e}"))
    out = [it for got in results if got for it in got]

    # 论文入口：先抓完别的信源，再看它们提到了哪些论文（D36）
    paper_src = next((s for s in sources if s.get("type") == "arxiv_mentions"), None)
    if paper_src:
        try:
            papers = collect_mentions(out, ctx.db, paper_src, ctx)
            if papers:
                print(f"  fetch [论文] 被提及才抓：{len(papers)} 篇")
            out += papers
        except Exception as ex:  # noqa: BLE001 论文入口失败不阻塞主流程
            ctx.note_error("fetch", f"arxiv_mentions: {ex}")

    # 全局安全阀：防止某天信源集体放量把预算打穿
    if ctx.cfg.max_items_per_run and len(out) > ctx.cfg.max_items_per_run:
        out.sort(key=lambda it: ("SABCD".index(it.tier) if it.tier in "SABCD" else 9))
        out = out[: ctx.cfg.max_items_per_run]
    # 合并而不是整个赋值：collect_mentions 先写了 arxiv_mentions，整个赋值会把它冲掉——
    # 闸门上线后第一次云端运行（20260918-010050）实际放进 1 篇，运行记录里却查不到，就是这样丢的
    ctx.stats.setdefault("fetch", {}).update({"total": len(out), "per_source": per_source})
    return out
