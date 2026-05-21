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
    preface, phrases = comic_series.parse_comic_input(
        "文前短语：想和你一起发会儿呆。\n第一辑：今晚月亮很忙\n第二辑：云路过窗外"
    )
    assert preface == "想和你一起发会儿呆"
    assert phrases == ["今晚月亮很忙", "云路过窗外"]
    title, preface, phrases = comic_series.parse_comic_input_parts(
        "书名：今晚不用变厉害\n文前短语：想和你一起发会儿呆。\n第一辑：今晚月亮很忙\n第二辑：云路过窗外"
    )
    assert title == "今晚不用变厉害"
    assert preface == "想和你一起发会儿呆"
    assert phrases == ["今晚月亮很忙", "云路过窗外"]
    with pytest.raises(ValueError):
        comic_series.validate_comic_phrases(["只有一条"])


def test_comic_image_prompt_hides_section_labels_and_locks_series_style() -> None:
    prompt, animal, subject = comic_series.build_comic_image_prompt(
        phrase="今晚月亮放下了手里的活。",
        index=1,
        total=5,
    )

    assert animal and subject
    assert "系列进度" not in prompt
    assert "第一辑" not in prompt
    assert "第一辑 / 共" not in prompt
    assert "画面中绝对不要出现" in prompt
    assert "章节顺序" in prompt
    assert "生成封面后，再根据目录中的5-6个章节短语" in prompt
    assert "所有配图必须与封面保持统一视觉风格" in prompt
    assert "已经生成的封面" in prompt
    assert "儿童绘本封面" not in prompt
    assert "原创 Q 版" not in prompt
    assert "封面主体：" not in prompt
    assert "同一本成年人治愈系情绪绘本" in prompt
    assert "漫画端口长期参考" not in prompt


def test_comic_image_prompts_keep_required_cover_and_series_constraints(
    tmp_path: Path,
) -> None:
    phrases = ["今晚月亮很忙", "云路过窗外", "心事晾在窗台", "什么都不必赶", "明天会发芽"]
    images = comic_series.plan_comic_section_images(
        phrases,
        tmp_path,
        title="今天不想当大人",
        preface_phrase="想和你一起发会儿呆",
    )
    by_name = {image.filename: image.prompt for image in images}

    cover_prompt = by_name["comic_cover.png"]
    assert "书名：今天不想当大人" in cover_prompt
    assert "根据主题与书名，先生成一张具有出版感的治愈系封面" in cover_prompt
    assert "整体偏日韩治愈系与文艺插画风格，简洁、有留白、有情绪氛围" in cover_prompt
    assert "可使用人物、动物、风景、城市、房间、街道、天气、植物、光影、食物、日常物件或抽象意象等元素" in cover_prompt
    assert "重点是“情绪感”与“意境”，而不是复杂剧情" in cover_prompt
    assert "色彩有统一的情绪基调与高级感，可适当使用高识别度配色" in cover_prompt
    assert "整体像豆瓣高分绘本、微信读书情绪封面、LOFT文创、ins治愈插画" in cover_prompt
    assert "避免低龄儿童绘本感、避免商业鸡汤海报感、避免AI模板化插画感" in cover_prompt

    for filename in ["comic_preface.png", "comic_section_01.png"]:
        prompt = by_name[filename]
        assert "生成封面后，再根据目录中的5-6个章节短语，分别创作对应的配图" in prompt
        for constraint in ["配色体系", "笔触质感", "构图语言", "情绪氛围", "留白方式", "光影风格", "插画颗粒感"]:
            assert constraint in prompt
        assert "每张配图根据章节短语表达不同情绪与意境，但仍属于同一本书的视觉体系" in prompt
        assert "画面不必重复角色，也不必强行出现人物，可根据短语自由选择意象" in prompt
        assert "重点是“短句的情绪可视化”，像一本成年人阅读的治愈系情绪绘本" in prompt
        assert "避免重复构图、重复元素、重复情绪表达" in prompt
        assert "整体克制、安静、松弛、有生活感与余味" in prompt
        assert "已经生成的封面保持同一视觉系统" in prompt


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
    preface_phrase = "想和你一起发会儿呆。"
    title = "今晚不用变厉害"
    image_prompts: list[str] = []
    gate_wait_timeouts: list[int] = []

    def fake_image(prompt: str, output_path: Path, **kwargs: object) -> Path:
        assert "避免" in prompt
        assert "系列进度" not in prompt
        assert "第一辑 / 共" not in prompt
        assert "画面中绝对不要出现" in prompt
        gate_wait_timeouts.append(int(kwargs.get("gate_wait_timeout_seconds") or 0))
        if output_path.name == "comic_cover.png":
            assert "根据主题与书名" in prompt
            assert "具有出版感的治愈系封面" in prompt
        elif output_path.name == "comic_preface.png":
            assert "文前短语配图" in prompt
            assert "所有配图必须与封面保持统一视觉风格" in prompt
        else:
            assert "所有配图必须与封面保持统一视觉风格" in prompt
            assert "短句的情绪可视化" in prompt
        image_prompts.append(prompt)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(PNG_1X1)
        return output_path

    async def fake_writer(prompt: str, **_: object) -> str:
        raise AssertionError("comic_series must not call OpenAI writer")

    async def fake_polish(text: str, **_: object) -> str:
        raise AssertionError("comic_series must not call Gemini polish")

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
            comic_series.comic_raw_text_from_phrases(
                comic_series.parse_comic_phrases(raw_text),
                preface_phrase=preface_phrase,
                title=title,
            ),
            category="comic_series",
            output_dir=output_dir,
        )
    )

    assert len(image_prompts) == 7
    assert gate_wait_timeouts == [comic_series._comic_image_gate_wait_timeout_seconds()] * 7
    writer_input = (output_dir / "00_comic_writer_input.md").read_text(encoding="utf-8")
    assert title in writer_input
    assert "{{COMIC_IMAGE_1}}" in writer_input
    assert "{{COMIC_PREFACE_IMAGE}}" in writer_input
    assert "封面主体设定" not in writer_input
    assert "@目录" in result
    assert "@最终书名" in result
    assert f"@最终书名\n{title}" in result
    assert "{{COMIC_PREFACE_IMAGE}}" not in result
    assert "{{COMIC_IMAGE_1}}" not in result
    assert "![第一辑配图]" not in result
    assert "![配图](comic_images/comic_preface.png)" in result
    assert "![配图](comic_images/comic_section_01.png)" in result
    assert "comic_images/comic_cover.png" not in result
    assert "comic_images/comic_preface.png" in result
    assert "comic_images/comic_section_01.png" in result
    assert "小熊说" not in result
    assert "Tonight" not in result
    assert (output_dir / "comic_images" / "comic_cover.png").exists()
    assert (output_dir / "comic_images" / "comic_preface.png").exists()
    assert (output_dir / "comic_images" / "comic_section_01.png").exists()
    assert (output_dir / "04_format.md").read_text(encoding="utf-8") == result


def test_healing_comic_category_uses_comic_pipeline_with_explicit_title(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    phrases = ["短语一", "短语二", "短语三", "短语四", "短语五"]
    title = "看见你我就笑"

    def fake_image(prompt: str, output_path: Path, **_: object) -> Path:
        assert title in prompt
        assert "想用一只原创小熊作为主角" in prompt
        assert "Buddy Gator" in prompt
        assert "禁止复刻" in prompt
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(PNG_1X1)
        return output_path

    monkeypatch.setattr(comic_series, "generate_image_file_from_prompt", fake_image)

    output_dir = tmp_path / "healing_comic"
    result = asyncio.run(
        content_engine.generate_full_book(
            comic_series.comic_raw_text_from_phrases(
                phrases,
                title=title,
                theme_requirement="想用一只原创小熊作为主角",
                preface_phrase="今天也有一点软",
            ),
            category="healing_comic",
            output_dir=output_dir,
        )
    )

    assert f"@最终书名\n{title}" in result
    assert "comic_images/comic_preface.png" in result
    writer_input = (output_dir / "00_comic_writer_input.md").read_text(encoding="utf-8")
    assert "想用一只原创小熊作为主角" in writer_input
    assert (output_dir / "comic_images" / "comic_cover.png").exists()


def test_comic_series_uncertain_image_does_not_fail_manuscript(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_text = "\n".join([f"短语 {index}" for index in range(1, 6)])

    def uncertain_image(*_: object, **__: object) -> Path:
        raise cover.ImageApiUncertainError("server disconnected")

    async def fake_writer(prompt: str, **_: object) -> str:
        raise AssertionError("comic_series must not call OpenAI writer")

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

    assert "comic_images/comic_preface.png" in result
    assert "comic_images/comic_section_01.png" in result
    assert not (output_dir / "comic_images" / "comic_cover.png").exists()
    assert not (output_dir / "comic_images" / "comic_preface.png").exists()
    assert not (output_dir / "comic_images" / "comic_section_01.png").exists()
    assert (output_dir / "comic_images" / "comic_cover_uncertain.json").exists()
    manifest = (output_dir / "comic_images" / "comic_images_manifest.json").read_text(
        encoding="utf-8"
    )
    assert '"status": "uncertain"' in manifest
    assert (output_dir / "04_format.md").exists()


def test_docx_export_embeds_comic_images(tmp_path: Path) -> None:
    image_dir = tmp_path / "comic_images"
    image_dir.mkdir()
    (image_dir / "comic_preface.png").write_bytes(PNG_1X1)
    (image_dir / "comic_section_01.png").write_bytes(PNG_1X1)

    data = _markdown_to_docx(
        "@文前\n文前短语：想和你一起发会儿呆。\n"
        "![配图](comic_images/comic_preface.png)\n想和你一起发会儿呆。\n"
        "@正文\n@1. 第一辑\n短语：今晚月亮放下了手里的活。\n"
        "![配图](comic_images/comic_section_01.png)\n小熊说：No rush.\n",
        image_base_dir=tmp_path,
    )

    with zipfile.ZipFile(BytesIO(data)) as docx:
        assert "word/media/image1.png" in docx.namelist()
        assert "word/media/image2.png" in docx.namelist()
        assert "word/_rels/document.xml.rels" in docx.namelist()
        document_xml = docx.read("word/document.xml")
        assert b"rIdImage1" in document_xml
        assert b"rIdImage2" in document_xml
        assert b"![" not in document_xml
