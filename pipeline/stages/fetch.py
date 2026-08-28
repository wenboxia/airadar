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


def _clean_text(html: str, limit: int) -> str:
    soup = BeautifulSoup(html or "", "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()
    main = soup.find("article") or soup.find("main") or soup
    text = re.sub(r"\s+", " ", main.get_text(" ")).strip()
    return text[:limit]


def _clean_markdown(md: str, limit: int) -> str:
    """Jina Reader 返回 markdown，导航链接要剥掉，否则正文被链接噪声淹没。"""
    body = md.split("Markdown Content:", 1)[-1]
    body = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", body)   # [文字](链接) → 文字
    body = re.sub(r"^\s*[*\-]\s.*$", "", body, flags=re.M)  # 导航项列表
    body = re.sub(r"[#>`|]", " ", body)
    return re.sub(r"\s+", " ", body).strip()[:limit]


def _fetch_direct(url: str, limit: int) -> str:
    resp = requests.get(url, headers=UA, timeout=20)
    resp.raise_for_status()
    return _clean_text(resp.text[:1_000_000], limit)


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
            text = fn(url, limit)
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
        if published and datetime.fromisoformat(published) < since:
            continue
        raw = ""
        if getattr(e, "content", None):
            raw = e.content[0].get("value", "")
        raw = raw or getattr(e, "summary", "")
        content = _clean_text(raw, ctx.cfg.content_max_chars)
        it = _make_item(e.link, e.title, src, published, content)
        it.extra["content_source"] = "rss"
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
        if published and datetime.fromisoformat(published) < arxiv_since:
            continue
        abstract = re.sub(r"\s+", " ", getattr(e, "summary", "")).strip()
        it = _make_item(e.link, e.title, src, published, abstract)
        authors = [a.get("name", "") for a in getattr(e, "authors", [])][:6]
        it.extra["authors"] = authors
        items.append(it)
    return items


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
        words = set(re.findall(r"[a-z0-9]+", title.lower()))
        if kws and not (words & set(kws)):
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
                src, repo.get("created_at", ""),
                (repo.get("description") or ""))
            it.extra["stars"] = repo.get("stargazers_count", 0)
            it.extra["language"] = repo.get("language")
            items.append(it)
        time.sleep(1)  # GitHub 未认证限速 10 次/分钟
    return items


_FETCHERS = {"rss": _fetch_rss, "arxiv": _fetch_arxiv,
             "hn": _fetch_hn, "github": _fetch_github}


def run(items: list, ctx: Context) -> list:
    with open(SOURCES_PATH, encoding="utf-8") as f:
        sources = yaml.safe_load(f)["sources"]
    since = datetime.now(timezone.utc) - timedelta(days=ctx.cfg.since_days)
    todo = [s for s in sources if s.get("tier") != "X"]
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
    # 全局安全阀：防止某天信源集体放量把预算打穿
    if ctx.cfg.max_items_per_run and len(out) > ctx.cfg.max_items_per_run:
        out.sort(key=lambda it: ("SABCD".index(it.tier) if it.tier in "SABCD" else 9))
        out = out[: ctx.cfg.max_items_per_run]
    ctx.stats["fetch"] = {"total": len(out), "per_source": per_source}
    return out
