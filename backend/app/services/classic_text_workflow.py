"""May four-stage manuscript pipeline on the current paid API clients.

Prompts, splitting, stage order and delivery normalization are frozen to
70c7ad8. API transport, response receipts and budget stops stay current.
"""
from __future__ import annotations
import asyncio
import hashlib
import json
import logging
import httpx
from pathlib import Path
from typing import Any, Callable
from app.core.config import settings
from app.prompts.classic_text import VERSION, base_category, get_prompts, load_auxiliary_template
from app.services.classic_text_normalization import normalize_final_markdown
from app.services import batch_spend
from app.services.gemini import GeminiMaxTokensError
from app.services.deepseek import DeepSeekMaxTokensError
StageCallback = Callable[[str, dict[str, Any]], None]
logger = logging.getLogger(__name__)
STOP_ERRORS = (batch_spend.BatchBudgetStop, TimeoutError, httpx.TimeoutException, httpx.NetworkError, GeminiMaxTokensError, DeepSeekMaxTokensError)

# Resolve the active wrapper at call time, including existing accounting context.
async def _route_writer_call(prompt: str) -> str:
    from app.services import content_engine
    return await content_engine._route_writer_call(prompt)

def _polish_call(text: str, *, provider: str, temperature: float, system_instruction: str | None) -> str:
    if provider == "gemini":
        from app.services import content_engine
        call = content_engine.polish_book_text if temperature == 0.3 else content_engine.refine_book_text
        return call(text, system_instruction=system_instruction)
    if provider != "deepseek":
        raise ValueError(f"Unknown classic polish provider: {provider}")
    from app.services.deepseek import generate_text
    logger.info("DeepSeek 润色调用 model=%s input_chars=%d", settings.DEEPSEEK_MODEL, len(text))
    return generate_text(text, model=settings.DEEPSEEK_MODEL, temperature=temperature,
                         system_instruction=system_instruction, max_output_tokens=65536,
                         fail_on_max_tokens=True, allow_partial_format=False)

def polish_book_text(text: str, *, provider: str = "deepseek", system_instruction: str | None = None) -> str:
    return _polish_call(text, provider=provider, temperature=0.3, system_instruction=system_instruction)

def refine_book_text(text: str, *, provider: str = "deepseek", system_instruction: str | None = None) -> str:
    return _polish_call(text, provider=provider, temperature=0.2, system_instruction=system_instruction)

def format_book_text(text: str, **kwargs) -> str:
    from app.services import content_engine
    return content_engine.format_book_text(text, **kwargs)

def normalize_category(category: str | None) -> str:
    return base_category(category or "social_science")

async def _repair_front_matter(raw_text: str, generated_markdown: str) -> str:
    template = load_auxiliary_template("repair_front_matter.md")
    return await _route_writer_call(template.format(raw_text=raw_text.strip(), generated_markdown=generated_markdown.strip()))

def _append_history_test_style_prompt(writer_prompt: str, *, category: str, style_reference: str | None, style_notes: str | None) -> str:
    style_reference = (style_reference or '').strip()
    style_notes = (style_notes or '').strip()
    if category != "web_short_story" or not (style_reference or style_notes):
        return writer_prompt
    template = load_auxiliary_template("history_style.md")
    if not (style_reference or '').strip():
        template = template.replace('- 参考对象：{style_reference}\n', '')
    if not (style_notes or '').strip():
        template = template.replace('- 补充要求：{style_notes}\n', '')
    return template.format(writer_prompt=writer_prompt.rstrip(), style_reference=style_reference, style_notes=style_notes)

def _prepare_cache(output_dir: Path | None, identity: dict) -> None:
    if output_dir is None:
        return
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "00_text_workflow.json"
    if path.exists():
        if json.loads(path.read_text(encoding="utf-8")) != identity:
            raise ValueError("Text workflow cache differs; explicitly regenerate before changing prompts or source")
    else:
        if any((output_dir / name).exists() for name in ("01_writer.md", "01b_writer_repaired.md", "02_polish.md", "03_refine.md", "04_format.md")):
            raise ValueError("Unversioned stages cannot be reused by the classic workflow; explicitly regenerate")
        path.write_text(json.dumps(identity, ensure_ascii=False, indent=2), encoding="utf-8")

def _split_text_by_paragraph(text: str, *, target_chars: int=1000) -> list[str]:
    """按完整段落切块，每块目标 ~target_chars 字。

    - 段落边界 = 一个或多个空行（保留 markdown 段落语义）
    - 不破坏段落：单个段落超过 target_chars 时独立成块
    - 否则贪心拼装到接近 target_chars
    """
    import re
    paragraphs = [p.strip() for p in re.split('\\n\\s*\\n', text) if p.strip()]
    if not paragraphs:
        return []
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for para in paragraphs:
        para_len = len(para)
        if current and current_len + para_len > target_chars:
            chunks.append('\n\n'.join(current))
            current = [para]
            current_len = para_len
        else:
            current.append(para)
            current_len += para_len
    if current:
        chunks.append('\n\n'.join(current))
    return chunks

async def _polish_by_paragraph_chunks(text: str, *, target_chars: int=1000, system_instruction: str | None=None, on_stage: StageCallback | None=None, output_dir: Path | None=None, provider: str="deepseek") -> str:
    """第一层润色：按完整段落切成 ~1000 字一块，每块独立调用 polish_book_text
    （都走同一份 GEMINI_FAST_POLISH_PROMPT），最终用空行拼接还原段落分隔。

    健壮性原则：一润是质量增强层，不是必经任务。任何单块失败都不应让整个任务挂掉。
    每块独立 try：成功用润色稿；失败或超时则用原 chunk 兜底，继续往下走。
    最后日志统计降级数量。
    """
    if not text or not text.strip():
        return text
    chunks = _split_text_by_paragraph(text, target_chars=target_chars)
    if not chunks:
        return text
    total = len(chunks)
    polished: list[str] = []
    fallback_count = 0

    async def _polish_once(chunk: str) -> str:
        return await asyncio.to_thread(polish_book_text, chunk, system_instruction=system_instruction, provider=provider)
    for idx, chunk in enumerate(chunks, 1):
        logger.info('文稿一润 块 %d/%d (%d 字)', idx, total, len(chunk))
        if on_stage:
            on_stage('polish_chunk', {'idx': idx, 'total': total, 'chars': len(chunk)})
        digest = hashlib.sha256(((system_instruction or '') + '\0' + chunk).encode()).hexdigest()
        cache_name = f'classic_polish_chunks/{idx:04d}-{digest}.md'
        cached = output_dir / cache_name if output_dir is not None else None
        if cached is not None and cached.is_file():
            polished.append(cached.read_text(encoding='utf-8'))
            continue
        try:
            result = await _polish_once(chunk)
            if not result or not result.strip():
                raise ValueError('Empty polish response')
            _dump_stage(output_dir, cache_name, result)
            polished.append(result)
            continue
        except STOP_ERRORS:
            raise
        except asyncio.TimeoutError as exc:
            first_err = f'超时（{exc.__class__.__name__}）'
        except Exception as exc:
            if provider == 'deepseek':
                raise
            first_err = f'{exc.__class__.__name__}: {exc}'
        fallback_count += 1
        logger.error('文稿一润 块 %d/%d 失败 [%s]，回退到原文继续', idx, total, first_err)
        polished.append(chunk)
        _dump_stage(output_dir, cache_name, chunk)
    if fallback_count:
        logger.warning('文稿一润完成但有降级 [成功 %d / 总 %d / 降级 %d 块]', total - fallback_count, total, fallback_count)
    return '\n\n'.join((p.strip() for p in polished if p and p.strip()))

def _front_matter_missing(text: str) -> bool:
    required_sections = ('最终书名', '最终简介', '目录', '导言', '正文')
    return not all((marker in text for marker in required_sections))

def _dump_stage(output_dir: Path | None, filename: str, content: str) -> None:
    """把每一层的产物落盘，便于审稿和定位是哪一层吃掉了内容。"""
    if output_dir is None or not content:
        return
    try:
        path = output_dir / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + '.tmp')
        temporary.write_text(content, encoding='utf-8')
        temporary.replace(path)
    except STOP_ERRORS:
        raise
    except Exception as exc:
        raise batch_spend.BatchBudgetStop(f'Paid stage could not be saved: {filename}; stopped to prevent replay') from exc

def _log_stage(stage: str, content: str, prev_chars: int | None=None) -> None:
    n = len(content)
    if prev_chars and prev_chars > 0:
        ratio = 100.0 * n / prev_chars
        logger.info('[stage %s] %d 字，较上一层保留 %.1f%%', stage, n, ratio)
        if ratio < 80.0:
            logger.warning('[stage %s] 字数缩减超过 20%%（%d → %d），可能存在过度删减，请检查 output_dir 下的中间产物对比', stage, prev_chars, n)
    else:
        logger.info('[stage %s] %d 字', stage, n)

async def generate_full_book(raw_text: str, category: str='social_science', output_dir: Path | None=None, on_stage: StageCallback | None=None, style_reference: str | None=None, style_notes: str | None=None) -> str:
    normalized_category = normalize_category(category)
    identity = {"version": VERSION, "base_category": normalized_category, "source_sha256": hashlib.sha256(raw_text.strip().encode()).hexdigest(), "prompt_sha256": hashlib.sha256(json.dumps(get_prompts(category), sort_keys=True).encode()).hexdigest(), "style_sha256": hashlib.sha256(json.dumps([style_reference, style_notes]).encode()).hexdigest()}
    identity["auxiliary_sha256"] = hashlib.sha256((load_auxiliary_template("repair_front_matter.md") + load_auxiliary_template("history_style.md")).encode()).hexdigest()
    # Pin existing jobs to their original transport; never mix paid stage caches.
    cached_identity = output_dir / "00_text_workflow.json" if output_dir is not None else None
    if cached_identity is not None and cached_identity.is_file():
        previous = json.loads(cached_identity.read_text(encoding="utf-8"))
        provider = previous.get("polish_provider", "gemini")
        if "polish_provider" in previous:
            identity["polish_provider"] = provider
            identity["polish_model"] = settings.DEEPSEEK_MODEL
    else:
        provider = "deepseek"
        identity.update(polish_provider=provider, polish_model=settings.DEEPSEEK_MODEL)
    if provider not in {"deepseek", "gemini"}:
        raise ValueError(f"Unknown classic polish provider: {provider}")
    _prepare_cache(output_dir, identity)
    prompts = get_prompts(normalized_category)
    full_book_writer_prompt = prompts['full_book_writer']
    gemini_fast_polish_prompt = prompts['gemini_fast_polish']
    gemini_fast_refine_prompt = prompts['gemini_fast_refine']
    final_book_format_prompt = prompts['final_book_format']
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)

    def _read_stage(filename: str) -> str | None:
        if output_dir is None:
            return None
        path = output_dir / filename
        if not path.exists():
            return None
        try:
            content = path.read_text(encoding='utf-8').strip()
            return content or None
        except STOP_ERRORS:
            raise
        except Exception as exc:
            logger.warning('读取中间产物 %s 失败: %s', filename, exc)
            return None
    generated_markdown = _read_stage('01b_writer_repaired.md') or _read_stage('01_writer.md')
    if generated_markdown:
        logger.info('复用已存在的 01_writer.md')
        _log_stage('1/4 writer (resume)', generated_markdown)
        if on_stage:
            on_stage('writer_done', {'chars': len(generated_markdown)})
    else:
        if on_stage:
            on_stage('writer_start', {})
        writer_prompt = full_book_writer_prompt.format(raw_text=raw_text.strip())
        writer_prompt = _append_history_test_style_prompt(writer_prompt, category=normalized_category, style_reference=style_reference, style_notes=style_notes)
        generated_markdown = await _route_writer_call(writer_prompt)
        _log_stage('1/4 writer', generated_markdown)
        _dump_stage(output_dir, '01_writer.md', generated_markdown)
        if on_stage:
            on_stage('writer_done', {'chars': len(generated_markdown)})
    if _front_matter_missing(generated_markdown):
        logger.warning('主模型初稿缺少前置块，尝试修复。')
        repaired_markdown = await _repair_front_matter(raw_text, generated_markdown)
        if repaired_markdown.strip():
            generated_markdown = repaired_markdown
            _log_stage('1b/4 writer-repaired', generated_markdown)
            _dump_stage(output_dir, '01b_writer_repaired.md', generated_markdown)
    writer_chars = len(generated_markdown)
    polished_markdown = _read_stage('02_polish.md')
    if polished_markdown:
        logger.info('复用已存在的 02_polish.md')
        _log_stage('2/4 polish (resume)', polished_markdown, prev_chars=writer_chars)
        polish_chars = len(polished_markdown)
        if on_stage:
            on_stage('polish_done', {'chars': polish_chars})
    else:
        if on_stage:
            on_stage('polish_start', {})
        polished_markdown = await _polish_by_paragraph_chunks(generated_markdown, target_chars=1000, system_instruction=gemini_fast_polish_prompt, on_stage=on_stage, output_dir=output_dir, provider=provider)
        _log_stage('2/4 polish', polished_markdown, prev_chars=writer_chars)
        _dump_stage(output_dir, '02_polish.md', polished_markdown)
        polish_chars = len(polished_markdown)
        if on_stage:
            on_stage('polish_done', {'chars': polish_chars})
    refined_markdown = _read_stage('03_refine.md')
    if refined_markdown:
        logger.info('复用已存在的 03_refine.md')
        _log_stage('3/4 refine (resume)', refined_markdown, prev_chars=polish_chars)
        refine_chars = len(refined_markdown)
        if on_stage:
            on_stage('refine_done', {'chars': refine_chars})
    else:
        if on_stage:
            on_stage('refine_start', {})
        try:
            refined_markdown = await asyncio.to_thread(refine_book_text, polished_markdown, system_instruction=gemini_fast_refine_prompt, provider=provider)
        except STOP_ERRORS:
            raise
        except Exception as exc:
            if provider == 'deepseek':
                raise
            logger.error('文稿二润失败，回退到一润结果继续: %s', exc)
            refined_markdown = polished_markdown
        _log_stage('3/4 refine', refined_markdown, prev_chars=polish_chars)
        _dump_stage(output_dir, '03_refine.md', refined_markdown)
        refine_chars = len(refined_markdown)
        if on_stage:
            on_stage('refine_done', {'chars': refine_chars})
    final_markdown = _read_stage('04_format.md')
    if final_markdown:
        logger.info('复用已存在的 04_format.md')
        _log_stage('4/4 format (resume)', final_markdown, prev_chars=refine_chars)
        if on_stage:
            on_stage('format_done', {'chars': len(final_markdown)})
    else:
        if on_stage:
            on_stage('format_start', {})
        try:
            final_markdown = await asyncio.to_thread(format_book_text, refined_markdown, prompt_template=final_book_format_prompt)
        except asyncio.TimeoutError as exc:
            raise TimeoutError(f'DeepSeek 终稿格式化超过 {settings.DEEPSEEK_FORMAT_CALL_TIMEOUT} 秒未返回') from exc
        _log_stage('4/4 format', final_markdown, prev_chars=refine_chars)
        _dump_stage(output_dir, '04_format.md', final_markdown)
        if on_stage:
            on_stage('format_done', {'chars': len(final_markdown)})
    if _front_matter_missing(final_markdown):
        logger.warning('后处理丢失前置块，回退到修复后的主模型输出。')
        final_markdown = generated_markdown
    if _front_matter_missing(final_markdown):
        logger.warning('前置块仍缺失，保留当前输出以避免整篇回退。')
    final_markdown = normalize_final_markdown(final_markdown)
    _dump_stage(output_dir, '04_format.md', final_markdown)
    logger.info('[summary] writer=%d → polish=%d → refine=%d → final=%d 字 (整体保留 %.1f%%)', writer_chars, polish_chars, refine_chars, len(final_markdown), 100.0 * len(final_markdown) / max(writer_chars, 1))
    if output_dir is not None:
        try:
            from app.core.quality_metrics import measure_quality, format_report
            report = measure_quality(final_markdown)
            report_text = format_report(report)
            report_path = output_dir / 'quality_report.txt'
            report_path.write_text(report_text, encoding='utf-8')
        except STOP_ERRORS:
            raise
        except Exception as exc:
            logger.warning('质量度量跳过: %s', exc)
    return final_markdown
