"""Real-world end-to-end test runner for Douyin/Kuaishou video -> book generation."""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from time import perf_counter

from app.core.config import settings
from app.core.workflow import run_full_workflow
from app.services.qingdou import QingdouClient, QingdouError, QingdouTimeoutError

_URL_RE = re.compile(r"https?://\S+")


def _read_input(prompt: str) -> str:
    while True:
        value = input(prompt).strip()
        if value:
            return value
        print("输入不能为空，请重新输入。")


def _extract_url_from_text(text: str) -> str:
    match = _URL_RE.search(text)
    if not match:
        raise ValueError("未检测到有效链接")
    return match.group(0).rstrip("，。；;),]』】'\"")


async def _extract_raw_text(douyin_url: str) -> tuple[str, int | None]:
    print("\n正在调用轻抖解析服务提取视频真实文案...")
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
            )
    except QingdouTimeoutError as exc:
        raise RuntimeError(f"轻抖解析超时：{exc}") from exc
    except QingdouError as exc:
        raise RuntimeError(f"轻抖服务返回失败：{exc}") from exc
    except ValueError as exc:
        raise RuntimeError(f"轻抖配置错误：{exc}") from exc
    except Exception as exc:
        raise RuntimeError(f"轻抖解析过程中发生未知错误：{exc}") from exc

    raw_text = (result.video_content or "").strip()
    if not raw_text:
        raise RuntimeError("轻抖解析成功但未返回可用文案")
    return raw_text, result.video_time


async def _run() -> None:
    douyin_input = _read_input("请输入抖音/快手视频分享链接 (douyin_url): ")

    try:
        douyin_url = _extract_url_from_text(douyin_input)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc

    extract_start = perf_counter()
    raw_text, video_time = await _extract_raw_text(douyin_url)
    extract_cost = perf_counter() - extract_start

    print("解析成功")
    print(f"视频字数: {len(raw_text)}")
    print(f"视频时长: {video_time if video_time is not None else '未知'} 秒")
    print(f"视频前100字: {raw_text[:100]}")
    print(f"轻抖解析耗时: {extract_cost:.2f} 秒")

    print("\n正在进入蓝图与正文生成工作流...")
    output_path = Path(__file__).resolve().parent / "output" / "real_douyin_test"
    workflow_start = perf_counter()
    book_path = await run_full_workflow(raw_text, output_path)
    workflow_cost = perf_counter() - workflow_start
    print(f"工作流耗时: {workflow_cost:.2f} 秒")
    report_path = output_path / "quality_report.txt"
    if report_path.exists():
        print(f"质量体检报告: {report_path}")
    if book_path is not None:
        print(f"\n已保存到: {book_path}")


def main() -> None:
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        print("\n用户取消执行。")
    except Exception as exc:
        print(f"\n执行失败：{exc}")


if __name__ == "__main__":
    main()
