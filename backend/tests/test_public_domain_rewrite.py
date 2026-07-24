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
