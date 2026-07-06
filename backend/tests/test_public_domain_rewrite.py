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
    _overlap_issue,
    rewrite_public_domain_fusion,
)


def _source(title: str, seed: str = "山中旧事") -> PublicDomainSource:
    text = (
        f"{seed}里有一位行路人，先在风雪中看见村庄，又在灯下听见老人说起往昔。"
        "这段文字用来构造足够长的公版样本，包含处境、选择、误会、醒悟和回望。"
    ) * 8
    return PublicDomainSource(
        title=title,
        author="佚名",
        publication_year=1900,
        rights_note="公版文本",
        text=text,
    )


def test_public_domain_rewrite_rejects_empty_sources(monkeypatch) -> None:
    monkeypatch.setattr(
        service,
        "deepseek_generate_text",
        lambda *args, **kwargs: pytest.fail("validation should happen before LLM call"),
    )

    with pytest.raises(PublicDomainRewriteError, match="1 到 3"):
        rewrite_public_domain_fusion(
            sources=[],
            public_domain_confirmed=True,
        )


def test_public_domain_rewrite_accepts_single_source_with_mocked_llm(monkeypatch) -> None:
    calls: list[str] = []

    def fake_generate(prompt: str, **kwargs) -> str:
        calls.append(prompt)
        if len(calls) == 1:
            return "@拟题\n窗边的风雨\n@核心问题\n孩子如何在孤独处境中重新组织自己的精神世界"
        return "窗边的风雨\n\n雨打在窗上，屋里的人声隔着门传来。孩子把书摊在膝上，看见荒凉海岸和远处的船影，也看见自己被排除在热闹之外的处境。后来，她没有照着旧句子复述这一切，而是把那种冷、怕和不服气，重新写成一段新的内心历程。"

    monkeypatch.setattr(service, "deepseek_generate_text", fake_generate)

    result = rewrite_public_domain_fusion(
        sources=[_source("来源一")],
        target_title="窗边的风雨",
        public_domain_confirmed=True,
    )

    assert result.title == "窗边的风雨"
    assert len(result.source_reports) == 1
    assert "单本/单篇公版同质改写" in calls[0]
    assert "实质不变、表达换形" in calls[0]
    assert "单来源同稿改写" in calls[1]
    assert "小说放进来，输出仍然必须是那本小说" in calls[1]
    assert "新的中心问题" not in calls[0]
    assert len(calls) == 2


def test_public_domain_rewrite_requires_public_domain_confirmation(monkeypatch) -> None:
    monkeypatch.setattr(
        service,
        "deepseek_generate_text",
        lambda *args, **kwargs: pytest.fail("validation should happen before LLM call"),
    )

    with pytest.raises(PublicDomainRewriteError, match="确认"):
        rewrite_public_domain_fusion(
            sources=[_source("来源一"), _source("来源二")],
            public_domain_confirmed=False,
        )


def test_public_domain_rewrite_rejects_copyright_markers(monkeypatch) -> None:
    monkeypatch.setattr(
        service,
        "deepseek_generate_text",
        lambda *args, **kwargs: pytest.fail("validation should happen before LLM call"),
    )
    restricted = PublicDomainSource(
        title="受限文本",
        text=("这是一段文本。版权所有，未经许可不得转载。" * 40),
        rights_note="公版文本",
    )

    with pytest.raises(PublicDomainRewriteError, match="版权限制"):
        rewrite_public_domain_fusion(
            sources=[restricted, _source("来源二")],
            public_domain_confirmed=True,
        )


def test_public_domain_rewrite_generates_with_mocked_llm(monkeypatch) -> None:
    calls: list[str] = []

    def fake_generate(prompt: str, **kwargs) -> str:
        calls.append(prompt)
        if len(calls) == 1:
            return "@拟题\n风雪归途\n@核心问题\n人在困局中如何重新理解选择"
        return "风雪归途\n\n一个旅人在山路上停下脚步。他先记起第一本书里的迟疑，又想起第二本书里的忍耐。后来，他没有照搬旧人的脚印，而是在新的处境里重新作出选择。"

    monkeypatch.setattr(service, "deepseek_generate_text", fake_generate)

    result = rewrite_public_domain_fusion(
        sources=[_source("来源一"), _source("来源二", "江边旧梦")],
        target_title="风雪归途",
        target_direction="写一篇关于选择和回望的新稿",
        public_domain_confirmed=True,
    )

    assert result.title == "风雪归途"
    assert result.source_reports[0].title == "来源一"
    assert "融合蓝图" not in result.manuscript
    assert "多来源同质编排" in calls[1]
    assert "不能创作另一本书" in calls[1]
    assert "创新重构" not in calls[0]
    assert len(calls) == 2


def test_overlap_issue_flags_exact_source_copy() -> None:
    source = _source("来源一")
    issue = _overlap_issue(source.text, source)

    assert issue is not None
    assert issue.ratio >= service.OVERLAP_RATIO_BLOCK


def test_generate_from_files_reads_supported_images_with_ocr(monkeypatch) -> None:
    calls: list[tuple[bytes, str]] = []

    def fake_ocr(content: bytes, *, filename: str = "") -> str:
        calls.append((content, filename))
        return "第一章\n这是图片 OCR 出来的公版正文。"

    monkeypatch.setattr(router, "ocr_image_bytes", fake_ocr)

    upload = UploadFile(filename="source.png", file=io.BytesIO(b"image-bytes"))
    text = asyncio.run(router._read_uploaded_text(upload))  # noqa: SLF001

    assert "图片 OCR" in text
    assert calls == [(b"image-bytes", "source.png")]
