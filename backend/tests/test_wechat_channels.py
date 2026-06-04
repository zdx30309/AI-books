from __future__ import annotations

import httpx

from app.services import wechat_channels
from app.services.wechat_channels import WeChatChannelsResult
from app.tasks import tasks as task_tasks


def test_extract_wechat_channels_text_from_preview_api(monkeypatch) -> None:
    payload = {
        "errCode": 0,
        "data": {
            "authorInfo": {"nickname": "Author Name"},
            "feedInfo": {
                "description": "desc text #topic",
                "jumpInfo": {"wording": "Shenzhen"},
                "likeCountFmt": "153",
                "favCountFmt": "246",
                "forwardCountFmt": "298",
                "commentCountFmt": "16",
            },
        },
    }

    def fake_post(url, **kwargs):
        assert url.endswith("/finder-preview/api/feed/get_feed_info")
        assert kwargs["json"]["shortUri"] == "A8yYHFLFrB"
        return httpx.Response(201, json=payload, request=httpx.Request("POST", url))

    monkeypatch.setattr(wechat_channels.httpx, "post", fake_post)

    result = wechat_channels.extract_wechat_channels_text("https://weixin.qq.com/sph/A8yYHFLFrB")

    assert result.source_id == "A8yYHFLFrB"
    assert result.author == "Author Name"
    assert "desc text #topic" in result.text
    assert "153" in result.text


def test_fetch_video_text_uses_wechat_channels_metadata_fallback(monkeypatch) -> None:
    def fail_qingdou(*args, **kwargs):
        raise RuntimeError("qingdou failed")

    def ok_fallback(source_url: str, **kwargs):
        assert source_url == "https://weixin.qq.com/sph/A8yYHFLFrB"
        return WeChatChannelsResult(
            text="wechat metadata fallback",
            source_id="A8yYHFLFrB",
            author="Author",
            description="desc",
            raw={},
        )

    monkeypatch.setattr(task_tasks, "extract_video_text_with_qingdou", fail_qingdou)
    monkeypatch.setattr(task_tasks, "extract_wechat_channels_text", ok_fallback)

    text = task_tasks._fetch_video_text(  # noqa: SLF001
        "#tag https://weixin.qq.com/sph/A8yYHFLFrB",
        {"source_platform": "wechat_channels"},
    )

    assert text == "wechat metadata fallback"
