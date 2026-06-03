from __future__ import annotations

from pathlib import Path
import sys

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services import wechat_article
from app.services.wechat_article import WeChatArticleError


SAMPLE_HTML = """
<!doctype html>
<html>
<head>
  <meta property="og:title" content="备用标题" />
</head>
<body>
  <h1 id="activity-name">  情绪消耗不是爱  </h1>
  <div id="js_name">  关系观察室  </div>
  <div id="js_content">
    <p>第一段，讲亲密关系里的长期消耗。</p>
    <p><span>第二段</span><span>继续说明边界感。</span></p>
    <p>第三段<br/>换行后继续。</p>
    <script>window.bad = "不要出现";</script>
    <style>.bad{display:none}</style>
  </div>
</body>
</html>
"""


def test_is_wechat_article_url() -> None:
    assert wechat_article.is_wechat_article_url("https://mp.weixin.qq.com/s/abc")
    assert wechat_article.is_wechat_article_url("https://mp.weixin.qq.com/s?__biz=abc")
    assert not wechat_article.is_wechat_article_url("https://example.com/s/abc")


def test_extract_wechat_article_parses_public_article(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponse:
        url = "https://mp.weixin.qq.com/s/abc"
        text = SAMPLE_HTML

        def raise_for_status(self) -> None:
            return None

    class FakeClient:
        def __init__(self, **_: object) -> None:
            pass

        def __enter__(self) -> "FakeClient":
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def get(self, url: str) -> FakeResponse:
            assert url == "https://mp.weixin.qq.com/s/abc"
            return FakeResponse()

    monkeypatch.setattr(wechat_article.httpx, "Client", FakeClient)
    monkeypatch.setattr(wechat_article, "MIN_ARTICLE_CHARS", 10)

    article = wechat_article.extract_wechat_article("https://mp.weixin.qq.com/s/abc")

    assert article.title == "情绪消耗不是爱"
    assert article.author == "关系观察室"
    assert "第一段，讲亲密关系里的长期消耗。" in article.text
    assert "第二段继续说明边界感。" in article.text
    assert "第三段\n换行后继续。" in article.text
    assert "不要出现" not in article.text


def test_extract_wechat_article_rejects_non_wechat_url() -> None:
    with pytest.raises(WeChatArticleError, match="微信公众号文章链接"):
        wechat_article.extract_wechat_article("https://example.com/article")
