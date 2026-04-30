from __future__ import annotations

import asyncio
from pathlib import Path

from app.core.config import settings
from app.services.content_engine import generate_full_book
from app.services.qingdou import QingdouClient, QingdouDurationExceededError, QingdouError, QingdouTimeoutError


async def run_full_workflow(
    raw_text: str,
    output_dir: Path,
    *,
    category: str = "social_science",
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)

    markdown = await generate_full_book(raw_text, category, output_dir=output_dir)
    book_path = output_dir / "book.md"
    book_path.write_text(markdown, encoding="utf-8")
    return book_path


async def run_douyin_workflow(
    douyin_url: str,
    output_dir: Path,
    *,
    category: str = "social_science",
) -> Path:
    if not settings.QINGDOU_API_KEY:
        raise RuntimeError("未配置 QINGDOU_API_KEY，无法调用轻抖解析服务")
    if not settings.QINGDOU_BASE_URL:
        raise RuntimeError("未配置 QINGDOU_BASE_URL，无法调用轻抖解析服务")

    try:
        with QingdouClient() as client:
            result = await asyncio.to_thread(
                client.extract_text,
                douyin_url,
                timeout_seconds=60,
                poll_interval_seconds=2.0,
                max_video_duration=settings.QINGDOU_MAX_VIDEO_DURATION,
            )
    except QingdouTimeoutError as exc:
        raise RuntimeError(f"轻抖解析超时：{exc}") from exc
    except QingdouDurationExceededError as exc:
        raise RuntimeError(f"视频时长超限：{exc}") from exc
    except QingdouError as exc:
        raise RuntimeError(f"轻抖服务返回失败：{exc}") from exc
    except ValueError as exc:
        raise RuntimeError(f"轻抖配置错误：{exc}") from exc
    except Exception as exc:
        raise RuntimeError(f"轻抖解析过程中发生未知错误：{exc}") from exc

    raw_text = (result.video_content or "").strip()
    if not raw_text:
        raise RuntimeError("轻抖解析成功但未返回可用文案")

    return await run_full_workflow(raw_text, output_dir, category=category)
