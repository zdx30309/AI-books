from __future__ import annotations

from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.api.routers import tasks as task_router
from app.services.wechat_article import WeChatArticle


def test_reference_article_config_is_fixed_to_emotion_relationship() -> None:
    article = WeChatArticle(
        source_url="https://mp.weixin.qq.com/s/abc",
        title="relationship article",
        author="observer",
        text="wechat body " * 200,
    )

    cfg, source_text = task_router._reference_article_config(article)

    assert source_text == article.text.strip()
    assert cfg["category"] == "emotion_relationship"
    assert cfg["input_source"] == "reference_article"
    assert cfg["reference_source"] == "wechat_article"
    assert cfg["wechat_article_url"] == article.source_url
    assert cfg["source_title"] == article.title
    assert cfg["title"] == article.title
