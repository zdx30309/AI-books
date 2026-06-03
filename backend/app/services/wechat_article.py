"""Extract public WeChat official account article text."""
from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)
MIN_ARTICLE_CHARS = 120
_WECHAT_HOSTS = {"mp.weixin.qq.com"}
_WHITESPACE_RE = re.compile(r"[ \t\f\v\u00a0\u200b]+")
_BLANK_LINES_RE = re.compile(r"\n{3,}")


class WeChatArticleError(RuntimeError):
    """Raised when a public WeChat article cannot be fetched or parsed."""


@dataclass(frozen=True)
class WeChatArticle:
    source_url: str
    text: str
    title: str | None = None
    author: str | None = None


def is_wechat_article_url(url: str) -> bool:
    try:
        parsed = urlparse((url or "").strip())
    except Exception:
        return False
    host = (parsed.netloc or "").lower().split("@")[-1].split(":")[0]
    return parsed.scheme in {"http", "https"} and (
        host in _WECHAT_HOSTS or host.endswith(".mp.weixin.qq.com")
    )


def extract_wechat_article(url: str, *, timeout_seconds: int = 25) -> WeChatArticle:
    source_url = (url or "").strip()
    if not is_wechat_article_url(source_url):
        raise WeChatArticleError("请填写有效的微信公众号文章链接")

    html_text = _fetch_wechat_html(source_url, timeout_seconds=timeout_seconds)
    title = _extract_first_section(html_text, "activity-name") or _extract_title_fallback(html_text)
    author = _extract_first_section(html_text, "js_name") or None
    article_text = _extract_first_section(html_text, "js_content")
    if not article_text:
        raise WeChatArticleError("未能从公众号页面识别到正文，可能是链接失效、需要登录或页面被限制访问")
    if len(article_text) < MIN_ARTICLE_CHARS:
        raise WeChatArticleError(
            f"公众号正文过短（{len(article_text)} 字符），请确认链接是完整公开文章"
        )
    return WeChatArticle(
        source_url=source_url,
        title=title or None,
        author=author or None,
        text=article_text,
    )


def _fetch_wechat_html(url: str, *, timeout_seconds: int) -> str:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
        "Referer": "https://mp.weixin.qq.com/",
    }
    try:
        with httpx.Client(
            timeout=httpx.Timeout(float(timeout_seconds), connect=8.0),
            headers=headers,
            follow_redirects=True,
            trust_env=False,
        ) as client:
            response = client.get(url)
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        raise WeChatArticleError(f"公众号文章抓取失败，HTTP 状态码 {status}") from exc
    except httpx.HTTPError as exc:
        raise WeChatArticleError(f"公众号文章抓取失败：{exc}") from exc
    text = response.text or ""
    if "mp.weixin.qq.com" not in str(response.url) and "js_content" not in text:
        logger.warning("WeChat article redirected outside mp.weixin.qq.com: %s", response.url)
    return text


def _extract_first_section(html_text: str, element_id: str) -> str:
    parser = _ElementTextParser(element_id)
    parser.feed(html_text or "")
    parser.close()
    return _normalize_text(parser.text)


def _extract_title_fallback(html_text: str) -> str:
    patterns = (
        r'<meta\s+property=["\']og:title["\']\s+content=["\']([^"\']+)["\']',
        r'<meta\s+name=["\']twitter:title["\']\s+content=["\']([^"\']+)["\']',
        r'var\s+msg_title\s*=\s*["\']([^"\']+)["\']',
    )
    for pattern in patterns:
        match = re.search(pattern, html_text or "", re.I)
        if match:
            return _normalize_text(match.group(1))
    return ""


def _normalize_text(value: str) -> str:
    text = html.unescape(value or "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _WHITESPACE_RE.sub(" ", text)
    lines = [line.strip() for line in text.split("\n")]
    text = "\n".join(line for line in lines if line)
    return _BLANK_LINES_RE.sub("\n\n", text).strip()


class _ElementTextParser(HTMLParser):
    _BLOCK_TAGS = {
        "address",
        "article",
        "blockquote",
        "br",
        "dd",
        "div",
        "dt",
        "figcaption",
        "figure",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "hr",
        "li",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "td",
        "th",
        "tr",
        "ul",
    }
    _SKIP_TAGS = {"script", "style", "svg", "canvas", "noscript"}

    def __init__(self, target_id: str) -> None:
        super().__init__(convert_charrefs=True)
        self.target_id = target_id
        self._depth = 0
        self._skip_depth = 0
        self._parts: list[str] = []
        self._done = False

    @property
    def text(self) -> str:
        return "".join(self._parts)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._done:
            return
        attr_map = {key.lower(): value or "" for key, value in attrs}
        if self._depth <= 0 and attr_map.get("id") == self.target_id:
            self._depth = 1
            self._append_break()
            return
        if self._depth <= 0:
            return
        self._depth += 1
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1
        if self._skip_depth:
            return
        if tag in self._BLOCK_TAGS:
            self._append_break()

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._done or self._depth <= 0 or self._skip_depth:
            return
        if tag in self._BLOCK_TAGS:
            self._append_break()

    def handle_endtag(self, tag: str) -> None:
        if self._done or self._depth <= 0:
            return
        if self._skip_depth:
            if tag in self._SKIP_TAGS:
                self._skip_depth -= 1
            self._depth -= 1
            return
        if tag in self._BLOCK_TAGS:
            self._append_break()
        self._depth -= 1
        if self._depth <= 0:
            self._done = True

    def handle_data(self, data: str) -> None:
        if self._done or self._depth <= 0 or self._skip_depth:
            return
        text = _WHITESPACE_RE.sub(" ", data or "")
        if text.strip():
            self._parts.append(text)

    def _append_break(self) -> None:
        if self._parts and not self._parts[-1].endswith("\n"):
            self._parts.append("\n")
