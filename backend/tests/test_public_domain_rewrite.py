from __future__ import annotations

import asyncio
import io
import sys
from pathlib import Path

import pytest
from fastapi import UploadFile

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.api.routers import public_domain_rewrite as router
from app.services import public_domain_rewrite as service
from app.services.public_domain_rewrite import (
    PublicDomainRewriteError,
    PublicDomainSource,
    _infer_source_mode,
    check_public_domain_manuscript,
    rewrite_public_domain_fusion,
)


def _source(title: str, seed: str = "山中旧事") -> PublicDomainSource:
    text = (
        f"{seed}里有一位行路人，先在风雪中看见村庄，又在灯下听见老人说起往昔。"
        "这段文字包含处境、选择、误会、醒悟和回望，用来构造公版测试样本。"
    ) * 8
    return PublicDomainSource(
        title=title,
        author="佚名",
        publication_year=1900,
        rights_note="公版文本",
        text=text,
    )


def test_public_domain_rewrite_allows_one_to_ten_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_generate(prompt: str, **_kwargs) -> str:
        calls.append(prompt)
        return "@拟题\n新作" if len(calls) == 1 else "独立成稿。" * 80

    monkeypatch.setattr(service, "deepseek_generate_text", fake_generate)
    result = rewrite_public_domain_fusion(
        sources=[_source(f"来源{i}") for i in range(10)],
        target_length=5_000,
        public_domain_confirmed=True,
    )
    assert len(result.source_reports) == 10
    assert len(calls) == 2
    assert "现代整理本" in calls[0]
    assert "重新选材" in calls[1]
    assert "朱雀" not in calls[1]


def test_story_sources_use_narrative_architecture(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_generate(prompt: str, **_kwargs) -> str:
        calls.append(prompt)
        return "故事蓝图" if len(calls) == 1 else "故事成稿。" * 100

    monkeypatch.setattr(service, "deepseek_generate_text", fake_generate)
    result = rewrite_public_domain_fusion(
        sources=[
            _source("古代佛教故事集"),
            _source("禅门公案故事"),
            _source("人物轶事选"),
            _source("佛学知识概论"),
        ],
        target_direction="以故事为主，写成连续可读的叙事作品",
        public_domain_confirmed=True,
    )
    assert result.source_mode == "narrative"
    assert result.source_mode_label == "故事叙事型"
    assert "65% 至 75%" in calls[0]
    assert "每个主要故事至少分成 4 至 8 个自然段" in calls[1]


def test_knowledge_source_does_not_force_story_ratio(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def fake_generate(prompt: str, **_kwargs) -> str:
        calls.append(prompt)
        return "知识蓝图" if len(calls) == 1 else "知识成稿。" * 100

    monkeypatch.setattr(service, "deepseek_generate_text", fake_generate)
    result = rewrite_public_domain_fusion(
        sources=[_source("传统文化知识全知道")],
        target_direction="解释核心概念与知识关系",
        public_domain_confirmed=True,
    )
    assert result.source_mode == "expository"
    assert "概念为主、例证为辅" in calls[0]
    assert "65% 至 75%" not in calls[1]


def test_tied_source_types_use_chapter_level_hybrid_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_generate(prompt: str, **_kwargs) -> str:
        calls.append(prompt)
        return "混合蓝图" if len(calls) == 1 else "混合成稿。" * 100

    monkeypatch.setattr(service, "deepseek_generate_text", fake_generate)
    result = rewrite_public_domain_fusion(
        sources=[_source("古代故事集"), _source("传统知识概论")],
        public_domain_confirmed=True,
    )
    assert result.source_mode == "hybrid"
    assert "按章分配写法" in calls[0]


@pytest.mark.parametrize(
    ("title", "direction", "expected"),
    [
        ("古代人物列传", "按史实与年代讲述生平", "historical"),
        ("传统经文注疏", "围绕原典做译注", "classical"),
        ("修习入门手册", "给出可执行的实践步骤", "practical"),
    ],
)
def test_remaining_source_modes_choose_distinct_architectures(
    title: str,
    direction: str,
    expected: str,
) -> None:
    profile = _infer_source_mode(
        [_source(title)],
        target_direction=direction,
        output_style=None,
    )

    assert profile.key == expected
    guidance = service._load_mode_guidance(profile.key)
    assert guidance
    assert "65% 至 75%" not in guidance


def test_story_collection_titles_win_over_generic_wisdom_and_sutra_words() -> None:
    profile = _infer_source_mode(
        [
            _source("佛学知识全知道"),
            _source("佛经的智慧"),
            _source("佛教·佛经故事"),
            _source("不可不知的佛教故事全集"),
            _source("佛家经典智慧故事"),
        ],
        target_direction="形成一部约五章、可以连续阅读的中文通俗读物",
        output_style="自然、准确，避免论文腔、百科腔和机械总结",
    )

    assert profile.key == "narrative"


def test_negative_style_words_do_not_override_source_mode() -> None:
    profile = _infer_source_mode(
        [_source("古代人物故事")],
        target_direction=None,
        output_style="避免百科腔，不要写成知识条目",
    )

    assert profile.key == "narrative"


def test_ambiguous_title_can_be_identified_from_story_body() -> None:
    source = PublicDomainSource(
        title="旧闻杂录",
        rights_note="公版文本",
        text=(
            "从前有一位老人来到山中，看见弟子在门前等候。弟子问道缘由，老人回答之后离去。"
            "有一天，国王听见此事，又来到寺中询问比丘。禅师看见来客，只说了一句话。"
        )
        * 10,
    )
    profile = _infer_source_mode(
        [source],
        target_direction=None,
        output_style=None,
    )

    assert profile.key == "narrative"


def test_narrative_sampling_removes_known_modern_anecdotes() -> None:
    modern = (
        "我出差回来，外甥正在阳台玩冰球。后来听说外甥结婚，我又到车站见他。"
        "那个小朋友患了重感冒，舅舅还说太不卫生。"
    ) * 30
    traditional = (
        "从前有一位比丘来到王城，国王问道修行之要，比丘回答后便离开。"
    ) * 30

    sample = service._sample_source(modern + traditional, source_mode="narrative")

    assert "冰球" not in sample
    assert "出差" not in sample
    assert "外甥" not in sample
    assert "比丘" in sample


def test_modern_anecdote_markers_force_manual_review() -> None:
    source = _source("传统故事")
    report = check_public_domain_manuscript(
        "他出差回来，发现外甥正在阳台玩冰球。" * 20,
        [source],
    )

    assert report["status"] == "needs_review"
    assert "冰球" in report["modern_narrative_issues"]


def test_public_domain_rewrite_removes_dashes_without_extra_model_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_generate(prompt: str, **_kwargs) -> str:
        calls.append(prompt)
        if len(calls) == 1:
            return "蓝图——故事在前——义理在后。"
        return "他停下来——不是因为畏惧——而是为了辨明方向。后来—他继续前行。"

    monkeypatch.setattr(service, "deepseek_generate_text", fake_generate)
    result = rewrite_public_domain_fusion(
        sources=[_source("古代人物故事")],
        target_direction="以故事为主",
        public_domain_confirmed=True,
    )

    assert len(calls) == 2
    assert "不得使用任何中文破折号" in calls[1]
    assert not any(mark in result.blueprint for mark in ("——", "—", "–"))
    assert not any(mark in result.manuscript for mark in ("——", "—", "–"))
    assert result.manuscript == "他停下来（不是因为畏惧）而是为了辨明方向。后来，他继续前行。"
    assert result.quality_report.startswith("状态：ok")


def test_public_domain_checker_rejects_remaining_dashes() -> None:
    report = check_public_domain_manuscript("仍有一句——用了破折号。" * 30, [_source("来源")])

    assert report["status"] == "needs_review"
    assert report["dash_issues"]


def test_public_domain_rewrite_rejects_empty_or_unconfirmed_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        service,
        "deepseek_generate_text",
        lambda *_args, **_kwargs: pytest.fail("validation must happen before an LLM call"),
    )
    with pytest.raises(PublicDomainRewriteError, match="1 到 10"):
        rewrite_public_domain_fusion(sources=[], public_domain_confirmed=True)
    with pytest.raises(PublicDomainRewriteError, match="权利"):
        rewrite_public_domain_fusion(sources=[_source("来源")], public_domain_confirmed=False)


def test_public_domain_rewrite_rejects_explicit_copyright_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        service,
        "deepseek_generate_text",
        lambda *_args, **_kwargs: pytest.fail("validation must happen before an LLM call"),
    )
    restricted = PublicDomainSource(
        title="受限文本",
        text=("这是一段文本。版权所有，未经许可不得转载。" * 40),
        rights_note="公版文本",
    )
    with pytest.raises(PublicDomainRewriteError, match="版权"):
        rewrite_public_domain_fusion(
            sources=[restricted, _source("来源二")],
            public_domain_confirmed=True,
        )


def test_overlap_report_is_local_only() -> None:
    source = _source("来源")
    copied = check_public_domain_manuscript(source.text, [source])
    independent = check_public_domain_manuscript("完全独立的组织方式。" * 500, [source])
    assert copied["status"] == "needs_review"
    assert independent["status"] == "ok"


def test_generate_from_files_reads_supported_images_with_ocr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[bytes, str]] = []

    def fake_ocr(content: bytes, *, filename: str = "") -> str:
        calls.append((content, filename))
        return "图片 OCR 出来的公版正文。"

    monkeypatch.setattr(router, "ocr_image_bytes", fake_ocr)
    upload = UploadFile(filename="source.png", file=io.BytesIO(b"image-bytes"))
    text = asyncio.run(router._read_uploaded_text(upload))
    assert "图片 OCR" in text
    assert calls == [(b"image-bytes", "source.png")]
