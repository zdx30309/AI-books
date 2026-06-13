"""Role-based model routing and logging for Epic Novel Engine."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import EpicGenerationLog


EpicModelRole = Literal["editor", "author", "cheap", "reference"]


@dataclass(frozen=True)
class EpicModelSelection:
    role: EpicModelRole
    provider: str
    model: str


def resolve_model(role: EpicModelRole) -> EpicModelSelection:
    if role == "editor":
        provider = settings.EPIC_EDITOR_MODEL_PROVIDER or "openai"
        model = settings.EPIC_EDITOR_MODEL_NAME or settings.OPENAI_WRITER_MODEL or settings.OPENAI_AUDITOR_MODEL
    elif role == "author":
        provider = settings.EPIC_AUTHOR_MODEL_PROVIDER or "deepseek"
        model = settings.EPIC_AUTHOR_MODEL_NAME or settings.LONG_NOVEL_DEFAULT_MODEL or settings.DEEPSEEK_MODEL
    elif role == "cheap":
        provider = settings.EPIC_CHEAP_MODEL_PROVIDER or "deepseek"
        model = settings.EPIC_CHEAP_MODEL_NAME or settings.LONG_NOVEL_CHEAP_MODEL or settings.DEEPSEEK_MODEL
    else:
        provider = settings.EPIC_REFERENCE_MODEL_PROVIDER or "deepseek"
        model = settings.EPIC_REFERENCE_MODEL_NAME or "deepseek-v4-pro"
    return EpicModelSelection(role=role, provider=provider, model=model)


def estimate_tokens(text: str | None) -> int:
    if not text:
        return 0
    return max(1, len(text) // 3)


def _run_async_text_call(coro) -> str:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(lambda: asyncio.run(coro)).result()


def _call_existing_writer(prompt: str, *, max_output_tokens: int | None = None) -> str:
    from app.services import content_engine

    return _run_async_text_call(
        content_engine._route_writer_call(
            prompt,
            reasoning_effort=settings.OPENAI_WRITER_REASONING_EFFORT,
            verbosity=settings.OPENAI_WRITER_VERBOSITY,
            max_output_tokens=max_output_tokens,
        )
    )


def _call_deepseek(prompt: str, *, model: str, max_output_tokens: int | None = None) -> str:
    from app.services.deepseek import generate_text

    if max_output_tokens is not None and int(max_output_tokens) <= 0:
        max_output_tokens = None
    return generate_text(prompt, model=model, max_output_tokens=max_output_tokens)


def strip_done_marker(text: str, marker: str | None = None) -> str:
    marker = marker or settings.EPIC_CONTINUATION_DONE_MARKER
    return (text or "").replace(marker, "").strip()


def needs_continuation(text: str, marker: str | None = None) -> bool:
    marker = marker or settings.EPIC_CONTINUATION_DONE_MARKER
    return bool(marker) and marker not in (text or "")


def assemble_continuation_chunks(
    first_chunk: str,
    next_chunk,
    *,
    marker: str | None = None,
    max_chunks: int | None = None,
) -> str:
    marker = marker or settings.EPIC_CONTINUATION_DONE_MARKER
    chunks = [first_chunk or ""]
    limit = settings.EPIC_CONTINUATION_MAX_CHUNKS if max_chunks is None else int(max_chunks)
    while needs_continuation(chunks[-1], marker):
        if limit > 0 and len(chunks) >= limit:
            break
        next_text = next_chunk("".join(chunks))
        if not next_text:
            break
        chunks.append(next_text)
    return strip_done_marker("".join(chunks), marker)


def log_epic_generation(
    db: Session,
    *,
    action: str,
    role: EpicModelRole = "cheap",
    project_id: UUID | None = None,
    bible_id: UUID | None = None,
    section_key: str | None = None,
    volume_number: int | None = None,
    chapter_number: int | None = None,
    prompt: str | None = None,
    completion: str | None = None,
    status: str = "success",
    error: str | None = None,
    estimated_cost: float | None = None,
    actual_cost: float | None = None,
) -> EpicGenerationLog:
    selection = resolve_model(role)
    log = EpicGenerationLog(
        project_id=project_id,
        action=action,
        provider=selection.provider,
        model=selection.model,
        bible_id=bible_id,
        section_key=section_key,
        volume_number=volume_number,
        chapter_number=chapter_number,
        prompt_tokens=estimate_tokens(prompt),
        completion_tokens=estimate_tokens(completion),
        estimated_cost=float(estimated_cost or 0.0),
        actual_cost=actual_cost,
        status=status,
        error=error,
    )
    db.add(log)
    db.flush()
    return log


def _release_connection_before_model_call(db: Session) -> None:
    try:
        if db.in_transaction():
            db.commit()
    except Exception:
        db.rollback()
        raise


def call_epic_model(
    db: Session,
    *,
    action: str,
    role: EpicModelRole,
    prompt: str,
    project_id: UUID | None = None,
    bible_id: UUID | None = None,
    section_key: str | None = None,
    volume_number: int | None = None,
    chapter_number: int | None = None,
    max_output_tokens: int | None = None,
    fallback_completion: str | None = None,
) -> str:
    selection = resolve_model(role)
    provider = (selection.provider or "").strip().lower()
    _release_connection_before_model_call(db)
    try:
        if provider in {"openai", "gpt", "chatgpt"} or provider.startswith("openai"):
            completion = _call_existing_writer(prompt, max_output_tokens=max_output_tokens)
        elif provider.startswith("deepseek"):
            completion = _call_deepseek(prompt, model=selection.model, max_output_tokens=max_output_tokens)
        else:
            raise RuntimeError(f"Unsupported Epic model provider: {selection.provider}")
    except Exception as exc:
        try:
            log_epic_generation(
                db,
                action=action,
                role=role,
                project_id=project_id,
                bible_id=bible_id,
                section_key=section_key,
                volume_number=volume_number,
                chapter_number=chapter_number,
                prompt=prompt,
                completion=fallback_completion or "",
                status="error",
                error=str(exc),
            )
            db.commit()
        except Exception:
            db.rollback()
            raise
        if fallback_completion is not None:
            return fallback_completion
        raise
    try:
        log_epic_generation(
            db,
            action=action,
            role=role,
            project_id=project_id,
            bible_id=bible_id,
            section_key=section_key,
            volume_number=volume_number,
            chapter_number=chapter_number,
            prompt=prompt,
            completion=completion,
            status="success",
        )
        db.commit()
        return completion
    except Exception as exc:
        db.rollback()
        raise RuntimeError("Epic model call succeeded but generation log write failed") from exc


def call_epic_model_with_continuation(
    db: Session,
    *,
    action: str,
    role: EpicModelRole,
    prompt: str,
    project_id: UUID | None = None,
    bible_id: UUID | None = None,
    section_key: str | None = None,
    volume_number: int | None = None,
    chapter_number: int | None = None,
    max_output_tokens: int | None = None,
    fallback_completion: str | None = None,
    done_marker: str | None = None,
) -> str:
    marker = done_marker or settings.EPIC_CONTINUATION_DONE_MARKER
    first = call_epic_model(
        db,
        action=action,
        role=role,
        prompt=prompt,
        project_id=project_id,
        bible_id=bible_id,
        section_key=section_key,
        volume_number=volume_number,
        chapter_number=chapter_number,
        max_output_tokens=max_output_tokens,
        fallback_completion=fallback_completion,
    )

    def _next(accumulated: str) -> str:
        continuation_prompt = (
            f"{prompt}\n\n"
            "你上一次输出尚未出现完成标记，说明可能被 provider 输出上限中断。"
            "请从上一段末尾继续写，不要重写已输出内容，完成后输出完成标记。\n\n"
            f"[已输出内容]\n{accumulated[-8000:]}"
        )
        return call_epic_model(
            db,
            action=f"{action}_continuation",
            role=role,
            prompt=continuation_prompt,
            project_id=project_id,
            bible_id=bible_id,
            section_key=section_key,
            volume_number=volume_number,
            chapter_number=chapter_number,
            max_output_tokens=max_output_tokens,
        )

    return assemble_continuation_chunks(first, _next, marker=marker)
