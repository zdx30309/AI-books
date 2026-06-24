from __future__ import annotations

from app.services import xiaohongshu
from app.tasks import tasks as task_tasks


def test_extract_xiaohongshu_page_text_uses_public_subtitles(monkeypatch) -> None:
    page_url = "https://www.xiaohongshu.com/discovery/item/6a0b2c620000000007029d40"
    subtitle_url = r"https:\u002F\u002Fexample.com\u002Fsubtitle_11.srt?sign=abc&t=123abc"
    html = f"""
    <html>
      <head>
        <meta property="og:videotime" content="57:37">
      </head>
      <body>
        <script>
          window.__INITIAL_STATE__ = {{"noteDetailMap":{{"abc":{{"note":{{
            "title":"性和爱能分开吗？当海王遇上真爱会怎么样？",
            "desc":"今天聊聊米兰·昆德拉《不能承受的生命之轻》。#深夜读书[话题]#",
            "user":{{"nickname":"周小辣"}},
            "consumer":{{"chapters":[
              {{"text":"引言：从200段性友谊开始","time":1000}},
              {{"time":106000,"text":"托马斯：当海王遇到真爱"}}
            ]}},
            "subtitles":[{{"language":"zh-CN","type":"source","url":"{subtitle_url}"}}],
            "duration":3458000
          }}}}}}}};
        </script>
      </body>
    </html>
    """
    srt = """1
00:00:00,000 --> 00:00:02,000
有这么一个男人，他只有单身的时候才觉得自在

2
00:00:02,000 --> 00:00:04,000
但是呢，他又离不开女人
"""

    def fake_fetch_text(url: str, *, timeout: int) -> str:
        if url == page_url:
            return html
        assert url == subtitle_url.replace(r"\u002F", "/")
        return srt

    monkeypatch.setattr(xiaohongshu, "_fetch_text", fake_fetch_text)

    result = xiaohongshu.extract_xiaohongshu_page_text(page_url)

    assert result.duration_seconds == 57 * 60 + 37
    assert result.title == "性和爱能分开吗？当海王遇上真爱会怎么样？"
    assert "标题：性和爱能分开吗？当海王遇上真爱会怎么样？" in result.text
    assert "作者：周小辣" in result.text
    assert "章节线索：" in result.text
    assert "00:01 引言：从200段性友谊开始" in result.text
    assert "正文：" in result.text
    assert "有这么一个男人，他只有单身的时候才觉得自在" in result.text
    assert "但是呢，他又离不开女人" in result.text


def test_xiaohongshu_qingdou_failure_falls_back_to_public_page(monkeypatch) -> None:
    source_url = "https://www.xiaohongshu.com/discovery/item/6a0b2c620000000007029d40"

    def fake_qingdou(*args, **kwargs):
        raise RuntimeError("Qingdou returned empty videoContent")

    monkeypatch.setattr(task_tasks, "extract_video_text_with_qingdou", fake_qingdou)
    monkeypatch.setattr(
        task_tasks,
        "extract_xiaohongshu_page_text",
        lambda *args, **kwargs: xiaohongshu.XiaohongshuPageText(
            text="正文：\n有这么一个男人，他只有单身的时候才觉得自在。",
            title="性和爱能分开吗？",
            duration_seconds=3457,
            subtitle_url="https://example.com/subtitle.srt",
        ),
    )

    text, duration = task_tasks._fetch_video_text_with_duration(  # noqa: SLF001
        source_url,
        {"category": "masterpiece_interpretation"},
    )

    assert "有这么一个男人" in text
    assert duration == 3457


def test_extract_xiaohongshu_page_text_rejects_caption_without_subtitle(monkeypatch) -> None:
    page_url = "https://www.xiaohongshu.com/discovery/item/abc"
    subtitle_url = r"https:\u002F\u002Fexample.com\u002Fsubtitle.srt?sign=abc&t=123abc"
    html = f"""
    <script>
      window.__INITIAL_STATE__ = {{"noteDetailMap":{{"abc":{{"note":{{
        "title":"只有标题不能当正文",
        "desc":"只有简介也不能当正文",
        "subtitles":[{{"language":"zh-CN","type":"source","url":"{subtitle_url}"}}]
      }}}}}}}};
    </script>
    """

    def fake_fetch_text(url: str, *, timeout: int) -> str:
        if url == page_url:
            return html
        raise RuntimeError("subtitle cdn failed")

    monkeypatch.setattr(xiaohongshu, "_fetch_text", fake_fetch_text)

    try:
        xiaohongshu.extract_xiaohongshu_page_text(page_url)
    except xiaohongshu.XiaohongshuPageTextError as exc:
        assert "字幕正文提取失败" in str(exc)
    else:
        raise AssertionError("caption-only Xiaohongshu fallback should fail")
