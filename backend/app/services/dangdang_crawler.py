"""Dangdang cover reference crawler for the cover workflow."""
from __future__ import annotations

import html
import json
import logging
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import quote_from_bytes

import httpx

logger = logging.getLogger(__name__)

DANGDANG_REFERENCE_BRANDS: dict[str, str] = {
    "酷威文化": "kuwei",
    "果麦文化": "guomai_wenhua",
    "时代华语": "shidai_huayu",
}

BRAND_SEARCH_ALIASES: dict[str, tuple[str, ...]] = {
    "酷威文化": ("酷威文化", "酷威"),
    "果麦文化": ("果麦文化", "果麦"),
    "时代华语": ("时代华语",),
}

_PRODUCT_RE = re.compile(r"(?:https?:)?//product\.dangdang\.com/(\d+)\.html", re.I)
_DDIMG_RE = re.compile(r"(?:https?:)?//img\d+m\d+\.ddimg\.cn/[^\"'<>\\\s]+?\.jpg", re.I)
_LI_RE = re.compile(r"<li\b[\s\S]*?</li>", re.I)
_TITLE_ATTR_RE = re.compile(r"<a\b[^>]*\btitle=[\"']([^\"']+)[\"'][^>]*>", re.I)
_NAME_BLOCK_RE = re.compile(r"<p\b[^>]*class=[\"'][^\"']*name[^\"']*[\"'][^>]*>([\s\S]*?)</p>", re.I)
_TAG_RE = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class DangdangCoverReference:
    brand: str
    title: str
    product_url: str
    image_url: str
    source: str
    product_id: str = ""
    source_page: str = ""
    local_image_path: str = ""


class DangdangCoverCrawler:
    """Small focused crawler inspired by MediaCrawler-style configurable collection."""

    def __init__(
        self,
        *,
        cache_dir: Path,
        timeout_seconds: int = 12,
        user_agent: str | None = None,
    ) -> None:
        self.cache_dir = cache_dir
        self.timeout_seconds = max(3, int(timeout_seconds or 12))
        self.user_agent = user_agent or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121 Safari/537.36"
        )

    def collect(
        self,
        *,
        title: str,
        intro: str,
        category_label: str,
        allowed_brands: Iterable[str],
        limit: int = 12,
        fetch_enabled: bool = True,
        download_images: bool = True,
        cache_enabled: bool = False,
    ) -> tuple[DangdangCoverReference, ...]:
        brands = [brand for brand in allowed_brands if brand in DANGDANG_REFERENCE_BRANDS]
        if not brands:
            return tuple()

        tokens = _reference_tokens(title, intro, category_label)
        per_brand_limit = max(1, limit // len(brands))
        references: list[DangdangCoverReference] = []
        seen_product_ids: set[str] = set()

        for brand in brands:
            candidates = self._load_local_brand_cache(brand, tokens) if cache_enabled else []
            if fetch_enabled and len(candidates) < per_brand_limit:
                candidates.extend(
                    self.search_brand(
                        brand=brand,
                        category_label=category_label,
                        limit=max(per_brand_limit * 2, 8),
                        download_images=download_images,
                        cache_enabled=cache_enabled,
                    )
                )
            selected = 0
            for item in candidates:
                dedupe_key = item.product_id or item.product_url
                if dedupe_key in seen_product_ids:
                    continue
                seen_product_ids.add(dedupe_key)
                references.append(item)
                selected += 1
                if selected >= per_brand_limit:
                    break

        if fetch_enabled and len(references) < min(6, limit):
            for brand in brands:
                for item in self.search_brand(
                    brand=brand,
                    category_label=category_label,
                    limit=8,
                    download_images=download_images,
                    cache_enabled=cache_enabled,
                ):
                    dedupe_key = item.product_id or item.product_url
                    if dedupe_key in seen_product_ids:
                        continue
                    seen_product_ids.add(dedupe_key)
                    references.append(item)
                    if len(references) >= limit:
                        break
                if len(references) >= limit:
                    break

        return tuple(references[:limit])

    def search_brand(
        self,
        *,
        brand: str,
        category_label: str,
        limit: int,
        download_images: bool,
        cache_enabled: bool = False,
    ) -> list[DangdangCoverReference]:
        if brand not in DANGDANG_REFERENCE_BRANDS:
            return []

        results: list[DangdangCoverReference] = []
        seen_product_ids: set[str] = set()
        queries = _brand_queries(brand, category_label)
        for query in queries:
            source_page = _dangdang_search_url(query)
            try:
                markup = self._fetch_search_page(source_page)
            except Exception as exc:
                logger.info("当当参考封面检索失败 brand=%s query=%s: %s", brand, query, exc)
                continue

            for item in self._parse_search_results(markup, brand=brand, source_page=source_page):
                if item.product_id in seen_product_ids:
                    continue
                seen_product_ids.add(item.product_id)
                if download_images and cache_enabled:
                    item = self._with_downloaded_image(item)
                if cache_enabled:
                    self._append_to_metadata_cache(item)
                results.append(item)
                if len(results) >= limit:
                    return results
            time.sleep(0.3)
        return results

    def _fetch_search_page(self, url: str) -> str:
        with httpx.Client(
            timeout=httpx.Timeout(self.timeout_seconds, connect=min(self.timeout_seconds, 5)),
            follow_redirects=True,
            headers={
                "User-Agent": self.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Referer": "https://www.dangdang.com/",
            },
        ) as client:
            response = client.get(url)
            response.raise_for_status()
        return response.content.decode("gbk", errors="ignore")

    def _parse_search_results(
        self,
        markup: str,
        *,
        brand: str,
        source_page: str,
    ) -> list[DangdangCoverReference]:
        chunks = _LI_RE.findall(markup)
        if not chunks:
            chunks = [markup]

        results: list[DangdangCoverReference] = []
        seen: set[str] = set()
        for chunk in chunks:
            product_match = _PRODUCT_RE.search(chunk)
            if not product_match:
                continue
            product_id = product_match.group(1)
            if product_id in seen:
                continue
            image_match = _DDIMG_RE.search(chunk)
            title = _extract_title(chunk) or f"{brand} 当当同类参考 {product_id}"
            image_url = _normalize_image_url(image_match.group(0)) if image_match else ""
            results.append(
                DangdangCoverReference(
                    brand=brand,
                    title=title[:160],
                    product_url=f"https://product.dangdang.com/{product_id}.html",
                    image_url=image_url,
                    product_id=product_id,
                    source="dangdang_live_search",
                    source_page=source_page,
                )
            )
            seen.add(product_id)
        return results

    def _load_local_brand_cache(
        self,
        brand: str,
        tokens: tuple[str, ...],
    ) -> list[DangdangCoverReference]:
        slug = DANGDANG_REFERENCE_BRANDS[brand]
        metadata_path = self.cache_dir / slug / "metadata.jsonl"
        if not metadata_path.exists():
            return []

        rows: list[tuple[int, int, DangdangCoverReference]] = []
        for line in metadata_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            title = str(item.get("title") or "").strip()
            product_url = str(item.get("product_url") or "").strip()
            image_url = str(item.get("image_url") or "").strip()
            product_id = str(item.get("product_id") or _product_id_from_url(product_url)).strip()
            index = int(item.get("index") or 0)
            if not title or not product_url:
                continue
            rows.append(
                (
                    _score_reference(title, tokens),
                    -index,
                    DangdangCoverReference(
                        brand=brand,
                        title=title[:160],
                        product_url=product_url,
                        image_url=image_url,
                        product_id=product_id,
                        source="local_dangdang_cache",
                        source_page=str(item.get("source_page") or ""),
                        local_image_path=self._find_local_image(slug, product_id),
                    ),
                )
            )
        if tokens:
            rows = [row for row in rows if row[0] > 0]
        rows.sort(key=lambda row: (row[0], row[1]), reverse=True)
        return [row[2] for row in rows]

    def _with_downloaded_image(self, item: DangdangCoverReference) -> DangdangCoverReference:
        if not item.image_url or not item.product_id:
            return item
        slug = DANGDANG_REFERENCE_BRANDS[item.brand]
        existing = self._find_local_image(slug, item.product_id)
        if existing:
            return _replace_reference(item, local_image_path=existing)

        brand_dir = self.cache_dir / slug
        brand_dir.mkdir(parents=True, exist_ok=True)
        image_path = brand_dir / f"{item.product_id}.jpg"
        try:
            with httpx.Client(timeout=60.0, headers={"User-Agent": self.user_agent}) as client:
                response = client.get(item.image_url)
                response.raise_for_status()
            image_path.write_bytes(response.content)
        except Exception as exc:
            logger.info("当当参考封面下载失败 product=%s: %s", item.product_id, exc)
            return item
        return _replace_reference(item, local_image_path=str(image_path))

    def _find_local_image(self, slug: str, product_id: str) -> str:
        if not product_id:
            return ""
        brand_dir = self.cache_dir / slug
        if not brand_dir.exists():
            return ""
        exact = brand_dir / f"{product_id}.jpg"
        if exact.exists():
            return str(exact)
        matches = sorted(brand_dir.glob(f"*_{product_id}.jpg"))
        return str(matches[0]) if matches else ""

    def _append_to_metadata_cache(self, item: DangdangCoverReference) -> None:
        if not item.product_id:
            return
        slug = DANGDANG_REFERENCE_BRANDS[item.brand]
        brand_dir = self.cache_dir / slug
        brand_dir.mkdir(parents=True, exist_ok=True)
        metadata_path = brand_dir / "metadata.jsonl"
        known_ids: set[str] = set()
        if metadata_path.exists():
            for line in metadata_path.read_text(encoding="utf-8").splitlines():
                try:
                    known_ids.add(str(json.loads(line).get("product_id") or ""))
                except json.JSONDecodeError:
                    continue
        if item.product_id in known_ids:
            return
        row = asdict(item)
        row["target_slug"] = slug
        row["target_label"] = item.brand
        with metadata_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def collect_dangdang_reference_covers(
    *,
    title: str,
    intro: str,
    category_label: str,
    allowed_brands: Iterable[str],
    cache_dir: Path,
    limit: int = 12,
    fetch_enabled: bool = True,
    download_images: bool = True,
    cache_enabled: bool = False,
    timeout_seconds: int = 12,
) -> tuple[DangdangCoverReference, ...]:
    crawler = DangdangCoverCrawler(cache_dir=cache_dir, timeout_seconds=timeout_seconds)
    return crawler.collect(
        title=title,
        intro=intro,
        category_label=category_label,
        allowed_brands=allowed_brands,
        limit=limit,
        fetch_enabled=fetch_enabled,
        download_images=download_images,
        cache_enabled=cache_enabled,
    )


def _dangdang_search_url(keyword: str) -> str:
    encoded = quote_from_bytes(keyword.encode("gbk", errors="ignore"))
    return f"https://search.dangdang.com/?key={encoded}&act=input"


def _brand_queries(brand: str, category_label: str) -> tuple[str, ...]:
    aliases = BRAND_SEARCH_ALIASES.get(brand, (brand,))
    queries: list[str] = []
    for alias in aliases:
        if category_label:
            queries.append(f"{alias} {category_label}")
        queries.append(alias)
    return tuple(dict.fromkeys(queries))


def _reference_tokens(title: str, intro: str, category_label: str) -> tuple[str, ...]:
    raw = " ".join([title or "", intro or "", category_label or ""])
    tokens: list[str] = []
    for token in re.split(r"[\s,，、。；;：:？！?（）()《》【】\[\]\"“”‘’]+", raw):
        clean = token.strip()
        if len(clean) >= 2:
            tokens.append(clean)
    return tuple(dict.fromkeys(tokens))


def _score_reference(title: str, tokens: tuple[str, ...]) -> int:
    score = 0
    for token in tokens:
        if token and token in title:
            score += min(len(token), 8)
    return score


def _extract_title(chunk: str) -> str:
    attr = _TITLE_ATTR_RE.search(chunk)
    if attr:
        return html.unescape(attr.group(1)).strip()
    name_block = _NAME_BLOCK_RE.search(chunk)
    if not name_block:
        return ""
    return html.unescape(_TAG_RE.sub("", name_block.group(1))).strip()


def _normalize_image_url(url: str) -> str:
    clean = html.unescape(url).strip()
    if clean.startswith("//"):
        return "https:" + clean
    if clean.startswith("http://"):
        return "https://" + clean.removeprefix("http://")
    return clean


def _product_id_from_url(url: str) -> str:
    match = _PRODUCT_RE.search(url or "")
    return match.group(1) if match else ""


def _replace_reference(item: DangdangCoverReference, **changes: str) -> DangdangCoverReference:
    data = asdict(item)
    data.update(changes)
    return DangdangCoverReference(**data)


__all__ = [
    "DANGDANG_REFERENCE_BRANDS",
    "DangdangCoverCrawler",
    "DangdangCoverReference",
    "collect_dangdang_reference_covers",
]
