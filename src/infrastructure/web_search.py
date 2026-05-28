"""API-key-free web search helpers for research agents."""

from __future__ import annotations

import asyncio
import json
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser
from xml.etree import ElementTree

from src.core.logging import get_logger

logger = get_logger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str
    provider: str


class _DuckDuckGoParser(HTMLParser):
    def __init__(self, provider: str):
        super().__init__()
        self.provider = provider
        self.results: list[SearchResult] = []
        self._in_title = False
        self._in_snippet = False
        self._current_href = ""
        self._title_parts: list[str] = []
        self._snippet_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {k: v or "" for k, v in attrs}
        class_name = attr.get("class", "")
        if tag == "a" and ("result__a" in class_name or class_name == "result-link"):
            self._in_title = True
            self._current_href = _clean_duckduckgo_url(attr.get("href", ""))
            self._title_parts = []
        if tag in {"a", "td", "div"} and (
            "result__snippet" in class_name or "result-snippet" in class_name
        ):
            self._in_snippet = True
            self._snippet_parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._in_title:
            self._in_title = False
            title = _clean_text(" ".join(self._title_parts))
            if title and self._current_href:
                self.results.append(
                    SearchResult(
                        title=title,
                        url=self._current_href,
                        snippet="",
                        provider=self.provider,
                    )
                )
        if self._in_snippet and tag in {"a", "td", "div"}:
            self._in_snippet = False
            snippet = _clean_text(" ".join(self._snippet_parts))
            if snippet and self.results and not self.results[-1].snippet:
                self.results[-1].snippet = snippet

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_parts.append(data)
        if self._in_snippet:
            self._snippet_parts.append(data)


async def search_web(query: str, *, max_results: int = 8) -> list[dict[str, str]]:
    """Search multiple no-key providers and return deduplicated results."""
    providers = [
        ("google_news_rss", _search_google_news_rss),
        ("global_tech_rss", _search_global_tech_rss),
        ("hacker_news_algolia", _search_hacker_news_algolia),
        ("dev_to_articles", _search_dev_to_articles),
        ("medium_tag_rss", _search_medium_tag_rss),
        ("duckduckgo_html", _search_duckduckgo_html),
        ("duckduckgo_lite", _search_duckduckgo_lite),
    ]

    tasks = [func(query, max_results=max_results) for _, func in providers]
    settled = await asyncio.gather(*tasks, return_exceptions=True)

    provider_results: list[list[SearchResult]] = []
    for result in settled:
        if isinstance(result, Exception):
            logger.debug("web_search.provider_failed", error=str(result)[:200])
            continue
        provider_results.append(result)

    merged: list[SearchResult] = []
    seen: set[str] = set()
    max_provider_items = max((len(result) for result in provider_results), default=0)
    for index in range(max_provider_items):
        for result in provider_results:
            if index >= len(result):
                continue
            item = result[index]
            key = _canonical_url(item.url)
            if key and key not in seen:
                seen.add(key)
                merged.append(item)
            if len(merged) >= max_results:
                break
        if len(merged) >= max_results:
            break

    return [item.__dict__ for item in merged[:max_results]]


async def _search_duckduckgo_html(query: str, *, max_results: int) -> list[SearchResult]:
    url = "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
    html = await asyncio.to_thread(_fetch_text, url)
    parser = _DuckDuckGoParser("duckduckgo_html")
    parser.feed(html)
    return parser.results[:max_results]


async def _search_duckduckgo_lite(query: str, *, max_results: int) -> list[SearchResult]:
    url = "https://lite.duckduckgo.com/lite/?" + urllib.parse.urlencode({"q": query})
    html = await asyncio.to_thread(_fetch_text, url)
    parser = _DuckDuckGoParser("duckduckgo_lite")
    parser.feed(html)
    return parser.results[:max_results]


async def _search_google_news_rss(query: str, *, max_results: int) -> list[SearchResult]:
    params = {
        "q": f"{query} when:30d",
        "hl": "en-US",
        "gl": "US",
        "ceid": "US:en",
    }
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode(params)
    xml_text = await asyncio.to_thread(_fetch_text, url)
    root = ElementTree.fromstring(xml_text)
    results = []
    for item in root.findall(".//item")[:max_results]:
        title = _clean_text(item.findtext("title", ""))
        link = item.findtext("link", "")
        pub_date = _clean_text(item.findtext("pubDate", ""))
        source = item.findtext("source", "")
        snippet = f"{source} {pub_date}".strip()
        if title and link:
            results.append(SearchResult(title, link, snippet, "google_news_rss"))
    return results


async def _search_global_tech_rss(query: str, *, max_results: int) -> list[SearchResult]:
    feeds = [
        ("TechCrunch", "https://techcrunch.com/feed/"),
        ("The Verge", "https://www.theverge.com/rss/index.xml"),
        ("VentureBeat", "https://venturebeat.com/feed/"),
        ("MIT Technology Review", "https://www.technologyreview.com/feed/"),
    ]
    terms = _query_terms(query)

    async def fetch_feed(source: str, url: str) -> list[tuple[int, SearchResult]]:
        xml_text = await asyncio.to_thread(_fetch_text, url)
        root = ElementTree.fromstring(xml_text)
        scored = []
        for item in root.findall(".//item"):
            title = _clean_text(item.findtext("title", ""))
            link = item.findtext("link", "")
            description = _strip_html(item.findtext("description", ""))
            pub_date = _clean_text(item.findtext("pubDate", ""))
            haystack = f"{title} {description}".lower()
            score = sum(1 for term in terms if term in haystack)
            if score and title and link:
                snippet = " ".join(part for part in (source, pub_date, description[:160]) if part)
                scored.append((score, SearchResult(title, link, snippet, "global_tech_rss")))
        return scored

    settled = await asyncio.gather(
        *(fetch_feed(source, url) for source, url in feeds),
        return_exceptions=True,
    )
    merged: list[tuple[int, SearchResult]] = []
    for result in settled:
        if isinstance(result, Exception):
            logger.debug("web_search.rss_feed_failed", error=str(result)[:160])
            continue
        merged.extend(result)
    merged.sort(key=lambda item: item[0], reverse=True)
    return [item for _, item in merged[:max_results]]


async def _search_gdelt_doc_api(query: str, *, max_results: int) -> list[SearchResult]:
    params = {
        "query": query,
        "mode": "ArtList",
        "format": "json",
        "maxrecords": str(max_results),
        "sort": "DateDesc",
        "timespan": "3months",
    }
    url = "https://api.gdeltproject.org/api/v2/doc/doc?" + urllib.parse.urlencode(params)
    payload = await asyncio.to_thread(_fetch_text, url, 12)
    data = json.loads(payload)
    results = []
    for item in data.get("articles", [])[:max_results]:
        title = _clean_text(str(item.get("title", "")))
        link = str(item.get("url", ""))
        source = _clean_text(str(item.get("sourceCountry", "") or item.get("domain", "")))
        date = _clean_text(str(item.get("seendate", "")))
        snippet = " ".join(part for part in (source, date) if part)
        if title and link:
            results.append(SearchResult(title, link, snippet, "gdelt_doc_api"))
    return results


async def _search_hacker_news_algolia(query: str, *, max_results: int) -> list[SearchResult]:
    params = {"query": query, "tags": "story", "hitsPerPage": str(max_results)}
    url = "https://hn.algolia.com/api/v1/search_by_date?" + urllib.parse.urlencode(params)
    payload = await asyncio.to_thread(_fetch_text, url)
    data = json.loads(payload)
    results = []
    for item in data.get("hits", [])[:max_results]:
        title = _clean_text(str(item.get("title") or item.get("story_title") or ""))
        link = str(item.get("url") or item.get("story_url") or "")
        author = _clean_text(str(item.get("author", "")))
        date = _clean_text(str(item.get("created_at", "")))
        snippet = " ".join(part for part in (author, date) if part)
        if title and link:
            results.append(SearchResult(title, link, snippet, "hacker_news_algolia"))
    return results


async def _search_dev_to_articles(query: str, *, max_results: int) -> list[SearchResult]:
    tag = _topic_tag(query, default="technology")
    params = {"tag": tag, "per_page": str(max_results), "top": "30"}
    url = "https://dev.to/api/articles?" + urllib.parse.urlencode(params)
    payload = await asyncio.to_thread(_fetch_text, url)
    data = json.loads(payload)
    results = []
    for item in data[:max_results]:
        title = _clean_text(str(item.get("title", "")))
        link = str(item.get("url", ""))
        date = _clean_text(str(item.get("published_at", "")))
        tags = _clean_text(", ".join(item.get("tag_list", [])[:4]))
        snippet = " ".join(part for part in (tags, date) if part)
        if title and link:
            results.append(SearchResult(title, link, snippet, "dev_to_articles"))
    return results


async def _search_medium_tag_rss(query: str, *, max_results: int) -> list[SearchResult]:
    tag = _medium_tag(query)
    url = f"https://medium.com/feed/tag/{urllib.parse.quote(tag)}"
    xml_text = await asyncio.to_thread(_fetch_text, url)
    root = ElementTree.fromstring(xml_text)
    results = []
    for item in root.findall(".//item")[:max_results]:
        title = _clean_text(item.findtext("title", ""))
        link = item.findtext("link", "")
        pub_date = _clean_text(item.findtext("pubDate", ""))
        creator = _clean_text(item.findtext("{http://purl.org/dc/elements/1.1/}creator", ""))
        snippet = " ".join(part for part in (creator, pub_date) if part)
        if title and link:
            results.append(SearchResult(title, link, snippet, "medium_tag_rss"))
    return results


def _fetch_text(url: str, timeout: int = 8) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _strip_html(value: str) -> str:
    return _clean_text(re.sub(r"<[^>]+>", " ", value or ""))


def _clean_duckduckgo_url(url: str) -> str:
    if not url:
        return ""
    parsed = urllib.parse.urlparse(url)
    params = urllib.parse.parse_qs(parsed.query)
    if "uddg" in params:
        return params["uddg"][0]
    return urllib.parse.urljoin("https://duckduckgo.com", url)


def _canonical_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    if not parsed.netloc:
        return ""
    return f"{parsed.netloc.lower()}{parsed.path}".rstrip("/")


def _topic_tag(query: str, *, default: str) -> str:
    lowered = query.lower()
    tag_map = [
        (("artificial intelligence", "generative ai", "genai", "llm", "ai "), "ai"),
        (("cloud", "aws", "azure", "gcp"), "cloud"),
        (("security", "cyber"), "security"),
        (("data", "analytics", "database"), "data"),
        (("startup", "venture", "funding"), "startup"),
        (("marketing", "growth"), "marketing"),
        (("product", "ux", "design"), "product"),
    ]
    for needles, tag in tag_map:
        if any(needle in lowered for needle in needles):
            return tag
    words = re.findall(r"[a-z][a-z0-9-]{2,}", lowered)
    return words[0] if words else default


def _query_terms(query: str) -> set[str]:
    stop_words = {
        "and",
        "for",
        "the",
        "with",
        "latest",
        "data",
        "market",
        "trend",
        "statistics",
        "benchmark",
        "report",
        "case",
        "study",
    }
    return {
        word for word in re.findall(r"[a-z][a-z0-9-]{2,}", query.lower()) if word not in stop_words
    }


def _medium_tag(query: str) -> str:
    lowered = query.lower()
    if any(
        term in lowered
        for term in ("artificial intelligence", "generative ai", "genai", "llm", "ai ")
    ):
        return "artificial-intelligence"
    if "cloud" in lowered:
        return "cloud-computing"
    if "startup" in lowered or "venture" in lowered:
        return "startup"
    if "design" in lowered or "ux" in lowered:
        return "design"
    return "technology"
