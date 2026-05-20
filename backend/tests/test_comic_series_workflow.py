from __future__ import annotations

import asyncio
from io import BytesIO
from pathlib import Path
import sys
import zipfile

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

from app.api.routers.tasks import _markdown_to_docx
from app.services import comic_series, content_engine, cover

PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000a49444154789c63600000020001e221bc3300000000"
    "49454e44ae426082"
)


def test_comic_series_requires_five_or_six_phrases() -> None:
    assert comic_series.parse_comic_phrases("1. 今晚月亮很忙\n2. 云路过窗外") == [
        "今晚月亮很忙",
        "云路过窗外",
    ]
    with pytest.raises(ValueError):
        comic_series.validate_comic_phrases(["只有一条"])


def test_comic_series_generates_images_and_skips_deepseek_format(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_text = "\n".join(
        [
            "今晚月亮放下了手里的活。",
            "我们也把心事晾在窗台。",
            "什么都不必赶。",
            "把小失落折成纸船。",
            "明天会从杯底发芽。",
        ]
    )
    writer_prompts: list[str] = []

    def fake_image(prompt: str, output_path: Path, **_: object) -> Path:
        assert "避免" in prompt
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(PNG_1X1)
        return output_path

    async def fake_writer(prompt: str, **_: object) -> str:
        writer_prompts.append(prompt)
        return """@最终书名
今天不想当大人
@最终简介
五辑治愈漫画绘本。
@文前
想和你一起发会儿呆。
@正文
@1. 第一辑
短语：今晚月亮放下了手里的活。
{{COMIC_IMAGE_1}}
小熊说：Tonight, I am off duty.
@2. 第二辑
短语：我们也把心事晾在窗台。
{{COMIC_IMAGE_2}}
小猫说：Let it dry by the window.
@3. 第三辑
短语：什么都不必赶。
{{COMIC_IMAGE_3}}
小狗说：No rush.
@4. 第四辑
短语：把小失落折成纸船。
{{COMIC_IMAGE_4}}
小兔说：It can float.
@5. 第五辑
短语：明天会从杯底发芽。
{{COMIC_IMAGE_5}}
小企鹅说：Maybe tomorrow.
"""

    async def fake_polish(text: str, **_: object) -> str:
        return text

    def fail_format(*_: object, **__: object) -> str:
        raise AssertionError("comic_series must not call DeepSeek final formatter")

    monkeypatch.setattr(comic_series, "generate_image_file_from_prompt", fake_image)
    monkeypatch.setattr(content_engine, "_route_writer_call", fake_writer)
    monkeypatch.setattr(content_engine, "_polish_by_paragraph_chunks_strict", fake_polish)
    monkeypatch.setattr(content_engine, "refine_book_text", lambda text, **_: text)
    monkeypatch.setattr(content_engine, "format_book_text", fail_format)

    output_dir = tmp_path / "comic_series"
    result = asyncio.run(
        content_engine.generate_full_book(
            raw_text,
            category="comic_series",
            output_dir=output_dir,
        )
    )

    assert writer_prompts and "{{COMIC_IMAGE_1}}" in writer_prompts[0]
    assert "@目录" not in result
    assert "{{COMIC_IMAGE_1}}" not in result
    assert "comic_images/comic_section_01.png" in result
    assert (output_dir / "comic_images" / "comic_section_01.png").exists()
    assert (output_dir / "04_format.md").read_text(encoding="utf-8") == result


def test_comic_series_uncertain_image_does_not_fail_manuscript(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_text = "\n".join([f"短语 {index}" for index in range(1, 6)])

    def uncertain_image(*_: object, **__: object) -> Path:
        raise cover.ImageApiUncertainError("server disconnected")

    async def fake_writer(prompt: str, **_: object) -> str:
        assert "{{COMIC_IMAGE_1}}" in prompt
        return """@最终书名
今天不想当大人
@最终简介
五辑治愈漫画绘本。
@文前
想和你一起发会儿呆。
@正文
@1. 第一辑
短语：短语 1
{{COMIC_IMAGE_1}}
小熊说：No rush.
@2. 第二辑
短语：短语 2
{{COMIC_IMAGE_2}}
小猫说：Let it dry.
@3. 第三辑
短语：短语 3
{{COMIC_IMAGE_3}}
小狗说：No hurry.
@4. 第四辑
短语：短语 4
{{COMIC_IMAGE_4}}
小兔说：It can float.
@5. 第五辑
短语：短语 5
{{COMIC_IMAGE_5}}
小企鹅说：Maybe tomorrow.
"""

    async def fake_polish(text: str, **_: object) -> str:
        return text

    monkeypatch.setattr(comic_series, "generate_image_file_from_prompt", uncertain_image)
    monkeypatch.setattr(content_engine, "_route_writer_call", fake_writer)
    monkeypatch.setattr(content_engine, "_polish_by_paragraph_chunks_strict", fake_polish)
    monkeypatch.setattr(content_engine, "refine_book_text", lambda text, **_: text)
    monkeypatch.setattr(content_engine, "format_book_text", lambda text, **_: text)

    output_dir = tmp_path / "comic_uncertain"
    result = asyncio.run(
        content_engine.generate_full_book(
            raw_text,
            category="comic_series",
            output_dir=output_dir,
        )
    )

    assert "comic_images/comic_section_01.png" in result
    assert not (output_dir / "comic_images" / "comic_section_01.png").exists()
    assert (output_dir / "comic_images" / "comic_section_01_uncertain.json").exists()
    manifest = (output_dir / "comic_images" / "comic_images_manifest.json").read_text(
        encoding="utf-8"
    )
    assert '"status": "uncertain"' in manifest
    assert (output_dir / "04_format.md").exists()


def test_docx_export_embeds_comic_images(tmp_path: Path) -> None:
    image_dir = tmp_path / "comic_images"
    image_dir.mkdir()
    (image_dir / "comic_section_01.png").write_bytes(PNG_1X1)

    data = _markdown_to_docx(
        "@正文\n@1. 第一辑\n短语：今晚月亮放下了手里的活。\n"
        "![第一辑配图](comic_images/comic_section_01.png)\n小熊说：No rush.\n",
        image_base_dir=tmp_path,
    )

    with zipfile.ZipFile(BytesIO(data)) as docx:
        assert "word/media/image1.png" in docx.namelist()
        assert "word/_rels/document.xml.rels" in docx.namelist()
        document_xml = docx.read("word/document.xml")
        assert b"rIdImage1" in document_xml
        assert b"![" not in document_xml
