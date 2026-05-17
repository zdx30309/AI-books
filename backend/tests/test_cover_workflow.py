from __future__ import annotations

import base64
import json
from pathlib import Path

import httpx

from app.services import cover, cover_jobs
from app.prompts.goal_driven_templates import list_categories
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

    assert brief.reference_site.startswith("当当网")
    assert brief.reference_brands == ("酷威文化", "果麦文化", "时代华语")
    assert "历史" in brief.matched_category_style
    assert "果麦文化" in brief.brand_style_direction
    assert "不要做成电商商品主图" in " ".join(brief.avoid_rules)
    assert {item["brand"] for item in brief.reference_samples} <= {"酷威文化", "果麦文化", "时代华语"}


def test_draft_and_refine_prompts_enforce_publication_cover_not_ecommerce() -> None:
    prompt = cover.build_cover_prompt(
        "醒来以后",
        "@最终简介\n一本帮助普通人处理焦虑和内耗的心理自助书。",
        author="测试作者",
        category="psychology_self_help",
    )

    assert "酷威文化" in prompt
    assert "果麦文化" in prompt
    assert "时代华语" in prompt
    assert "完整正面封面本体" in prompt
    assert "不要 3D 书模" in prompt
    assert "不要出现出版社 logo" in prompt
    assert "出版品牌 logo" in prompt
    assert "不要生成乱码中文" in prompt
    assert "治愈系手绘封面，日韩治愈插画风，图片避免抄袭和侵权。" in prompt


def test_all_cover_categories_have_prompt_adjustment_interface() -> None:
    for item in list_categories():
        adjustment = cover.get_cover_category_prompt_adjustment(item["id"])
        assert set(adjustment) == {"style_brief_rules", "draft_rules", "refine_rules"}


def test_healing_handdrawn_prompt_only_applies_to_psychology_cover_category() -> None:
    prompt = cover.build_cover_prompt(
        "沉默的账单",
        "@最终简介\n一本讨论普通人现实压力的社会痛点书。",
        author="测试作者",
        category="social_science",
    )

    assert "治愈系手绘封面，日韩治愈插画风，图片避免抄袭和侵权。" not in prompt


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
    assert brief["reference_site"].startswith("当当网")
    assert brief["reference_brands"] == ["酷威文化", "果麦文化", "时代华语"]
    assert {item["brand"] for item in brief["reference_samples"]} <= {"酷威文化", "果麦文化", "时代华语"}
    assert "时代华语" in brief["brand_style_direction"]
    assert "做成时代华语式心理自助书封面" in brief["generation_prompt_addendum"]
    assert "治愈系手绘封面，日韩治愈插画风，图片避免抄袭和侵权。" in brief["generation_prompt_addendum"]
    assert calls[0][0] == "generation"
    assert calls[1][0] == "edit"
    assert "可直接商用、具有成熟出版质感" in calls[1][1]
    assert "删除所有出版社 logo" in calls[1][1]
    assert "治愈系手绘封面，日韩治愈插画风，图片避免抄袭和侵权。" in calls[1][1]


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


def test_image_request_retries_transient_520(monkeypatch) -> None:
    class FakeHttp:
        def __init__(self) -> None:
            self.calls = 0

        def post(self, endpoint: str, **kwargs):
            self.calls += 1
            request = httpx.Request("POST", endpoint)
            if self.calls == 1:
                return httpx.Response(520, text="temporary edge error", request=request)
            return httpx.Response(
                200,
                json={"data": [{"b64_json": base64.b64encode(PNG_BYTES).decode("ascii")}]},
                request=request,
            )

    fake = FakeHttp()
    monkeypatch.setattr(cover.settings, "IMAGE_API_MAX_RETRIES", 2)
    monkeypatch.setattr(cover.time, "sleep", lambda _seconds: None)

    result = cover._post_image_request(fake, "https://api.example.test/images/edits", "key")  # noqa: SLF001

    assert result == PNG_BYTES
    assert fake.calls == 2


def test_delete_cover_job_items_marks_single_cover_deleted(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(cover_jobs, "COVER_JOBS_ROOT", tmp_path)
    status = cover_jobs.create_cover_job(
        "user-1",
        "covers.csv",
        [
            {"title": "第一本书", "author": "作者A", "category": "social_science", "category_label": "社会痛点"},
            {"title": "第二本书", "author": "作者B", "category": "life_philosophy", "category_label": "人生哲学"},
        ],
        default_category="social_science",
    )
    job_id = status["job_id"]
    image = cover_jobs.image_path(job_id, 1, "第一本书")
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(PNG_BYTES)

    updated = cover_jobs.delete_cover_job_items(job_id, [1])
    public = cover_jobs.public_cover_job_status(updated)
    raw = cover_jobs.read_status(job_id)

    assert not image.exists()
    assert raw["items"][0]["status"] == "deleted"
    assert [item["title"] for item in public["items"]] == ["第二本书"]
    assert public["total"] == 1


def test_retry_cover_job_items_preserves_draft_and_requeues_failed_cover(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(cover_jobs, "COVER_JOBS_ROOT", tmp_path)
    status = cover_jobs.create_cover_job(
        "user-1",
        "covers.csv",
        [
            {"title": "第一本书", "author": "作者A", "category": "social_science", "category_label": "社会痛点"},
        ],
        default_category="social_science",
    )
    job_id = status["job_id"]
    image = cover_jobs.image_path(job_id, 1, "第一本书")
    work_dir = image.parent / f".{image.stem}_work"
    work_dir.mkdir(parents=True, exist_ok=True)
    draft = work_dir / "draft_cover.png"
    draft.write_bytes(PNG_BYTES)
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(PNG_BYTES)

    status["items"][0].update(status="failed", error="temporary 520", auto_retry_count=3)
    cover_jobs.write_status(job_id, status)

    updated = cover_jobs.retry_cover_job_items(job_id, [1])

    assert updated["items"][0]["status"] == "pending"
    assert updated["items"][0]["error"] is None
    assert updated["items"][0]["auto_retry_count"] == 0
    assert not image.exists()
    assert draft.exists()


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
