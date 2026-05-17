from __future__ import annotations

import base64
import json
from pathlib import Path

from app.services import cover
from app.services.dangdang_crawler import DANGDANG_REFERENCE_BRANDS, DangdangCoverCrawler


PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


def test_style_brief_locks_reference_site_and_brands() -> None:
    cover_input = cover.prepare_cover_input(
        "暗处的时代",
        "@最终简介\n一个历史人物在制度缝隙中的命运转折。",
        author="测试作者",
        category="web_short_story",
    )

    brief = cover.build_cover_style_brief(cover_input)

    assert brief.reference_site == "当当网"
    assert brief.reference_brands == ("酷威文化", "果麦文化", "时代华语")
    assert "历史" in brief.matched_category_style
    assert "果麦文化" in brief.brand_style_direction
    assert "不要做成当当商品主图" in " ".join(brief.avoid_rules)
    assert {item["brand"] for item in brief.reference_samples} <= {"酷威文化", "果麦文化", "时代华语"}


def test_draft_and_refine_prompts_enforce_publication_cover_not_ecommerce() -> None:
    prompt = cover.build_cover_prompt(
        "醒来以后",
        "@最终简介\n一本帮助普通人处理焦虑和内耗的心理自助书。",
        author="测试作者",
        category="psychology_self_help",
    )

    assert "酷威文化、果麦文化、时代华语" in prompt
    assert "完整的正面封面本体" in prompt
    assert "不要 3D 书模" in prompt
    assert "不要出版社 logo" in prompt
    assert "不要出版品牌 logo" in prompt
    assert "不要乱码文字" in prompt


def test_generate_cover_file_writes_three_stage_artifacts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: list[tuple[str, str, str | None]] = []

    def fake_generation(prompt: str) -> bytes:
        calls.append(("generation", prompt, None))
        return PNG_BYTES

    def fake_edit(image_path: Path, prompt: str) -> bytes:
        assert image_path.name == "draft_cover.png"
        assert image_path.exists()
        calls.append(("edit", prompt, image_path.name))
        return PNG_BYTES

    monkeypatch.setattr(cover, "_request_image_generation", fake_generation)
    monkeypatch.setattr(cover, "_request_image_edit", fake_edit)

    result = cover.generate_cover_file(
        "醒来以后",
        "@最终简介\n一本帮助普通人处理焦虑和内耗的心理自助书。",
        tmp_path,
        category="psychology_self_help",
        author="测试作者",
    )

    assert result == tmp_path / "cover.png"
    assert (tmp_path / "draft_cover.png").exists()
    assert (tmp_path / "cover.png").exists()
    assert (tmp_path / "cover_draft_prompt.txt").exists()
    assert (tmp_path / "cover_refine_prompt.txt").exists()

    brief = json.loads((tmp_path / "cover_style_brief.json").read_text(encoding="utf-8"))
    assert brief["reference_site"] == "当当网"
    assert brief["reference_brands"] == ["酷威文化", "果麦文化", "时代华语"]
    assert {item["brand"] for item in brief["reference_samples"]} <= {"酷威文化", "果麦文化", "时代华语"}
    assert "时代华语" in brief["brand_style_direction"]
    assert calls[0][0] == "generation"
    assert calls[1][0] == "edit"
    assert "可直接商用，具有出版质感" in calls[1][1]
    assert "删除或避免所有出版社 logo" in calls[1][1]


def test_generate_cover_file_does_not_downgrade_to_draft_on_refine_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    def fake_generation(prompt: str) -> bytes:
        return PNG_BYTES

    def fake_edit(image_path: Path, prompt: str) -> bytes:
        raise RuntimeError("edit failed")

    monkeypatch.setattr(cover, "_request_image_generation", fake_generation)
    monkeypatch.setattr(cover, "_request_image_edit", fake_edit)

    try:
        cover.generate_cover_file(
            "醒来以后",
            "@最终简介\n一本心理自助书。",
            tmp_path,
            category="psychology_self_help",
            author="测试作者",
        )
    except RuntimeError as exc:
        assert "封面精修失败" in str(exc)
    else:
        raise AssertionError("refine failure should not be silently downgraded")

    assert (tmp_path / "draft_cover.png").exists()
    assert not (tmp_path / "cover.png").exists()
    assert (tmp_path / "cover_refine_error.txt").exists()


def test_dangdang_crawler_extracts_reference_cover_metadata(tmp_path: Path) -> None:
    crawler = DangdangCoverCrawler(cache_dir=tmp_path, timeout_seconds=3)
    markup = """
    <ul>
      <li id="p29910669">
        <a title="蛤蟆先生去看心理医生" href="//product.dangdang.com/29910669.html"></a>
        <img data-original="//img3m9.ddimg.cn/71/33/29910669-1_b_1751602431.jpg" />
      </li>
    </ul>
    """

    refs = crawler._parse_search_results(  # noqa: SLF001
        markup,
        brand="果麦文化",
        source_page="https://search.dangdang.com/?key=test&act=input",
    )

    assert len(refs) == 1
    assert refs[0].brand == "果麦文化"
    assert refs[0].product_id == "29910669"
    assert refs[0].title == "蛤蟆先生去看心理医生"
    assert refs[0].image_url.startswith("https://img3m9.ddimg.cn/")


def test_dangdang_crawler_does_not_write_cache_when_disabled(tmp_path: Path, monkeypatch) -> None:
    crawler = DangdangCoverCrawler(cache_dir=tmp_path, timeout_seconds=3)
    brand = next(iter(DANGDANG_REFERENCE_BRANDS))
    markup = """
    <ul>
      <li id="p29910669">
        <a title="reference title" href="//product.dangdang.com/29910669.html"></a>
        <img data-original="//img3m9.ddimg.cn/71/33/29910669-1_b_1751602431.jpg" />
      </li>
    </ul>
    """

    monkeypatch.setattr(crawler, "_fetch_search_page", lambda url: markup)

    refs = crawler.collect(
        title="test title",
        intro="test intro",
        category_label="test category",
        allowed_brands=[brand],
        limit=1,
        fetch_enabled=True,
        download_images=True,
        cache_enabled=False,
    )

    assert len(refs) == 1
    assert refs[0].local_image_path == ""
    assert list(tmp_path.rglob("*")) == []
