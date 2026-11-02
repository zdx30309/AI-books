"""The existing 1.0 subject pipeline followed by one Astra rewrite."""
import asyncio
import hashlib
import json
from pathlib import Path
from app.core.config import settings
from app.prompts.astra_agents2610 import AGENTS, MODEL, render_prompt, validate_workflow
from app.services import classic_text_workflow, openai_manuscript_audit as audit
from app.services.social_pain2610_workflow import _rewrite
from app.services.batch_spend import BatchBudgetStop
from app.utils.distributed_limiter import distributed_semaphore


def _generate_direct(raw_text: str, *, category: str, version: str, output_dir: Path) -> str:
    from app.services import content_engine as engine
    prompt = render_prompt(raw_text, workflow_version=version)
    identity = {"version": version, "category": category, "model": MODEL,
                "reasoning_mode": "standard", "reasoning_effort": "high",
                "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest()}
    manifest = output_dir / "00_text_workflow.json"
    if manifest.exists():
        if json.loads(manifest.read_text()) != identity:
            raise BatchBudgetStop("Astra直出缓存与提示词或原稿不一致，请明确重新生成")
    elif any(output_dir.glob("0[1-5]_*.md")) or any((output_dir / "openai_usage").glob("*.json")):
        raise BatchBudgetStop("Astra直出拒绝混用其他流程产物")
    audit.save(manifest, identity)
    with distributed_semaphore("openai-writer", limit=settings.OPENAI_WRITER_MAX_CONCURRENT_CALLS):
        response = audit.create(engine._get_openai_writer_client(), {
            "model": MODEL, "input": prompt, "reasoning": {"mode": "standard", "effort": "high"},
            "max_output_tokens": 65536}, action="astra_direct")
    result = audit.attr(response, "output_text") or engine._extract_openai_response_text(response)
    # These are byte-identical delivery aliases, not additional LLM stages.
    for name in ("01_writer.md", "04_format.md"):
        path = output_dir / name
        if path.exists() and path.read_text() != result:
            raise BatchBudgetStop("Astra直出阶段文件与已付费响应不一致，暂停核对")
        classic_text_workflow._dump_stage(output_dir, name, result)
    return result

async def generate_full_book(raw_text, *, category, workflow_version: str | None = None, output_dir: Path | None = None, on_stage=None):
    agent = AGENTS[category]
    version = workflow_version or agent["version"]
    validate_workflow(category, version)
    if output_dir is None:
        raise ValueError("2610智能体需要独立输出目录")
    if agent.get("direct"):
        output_dir.mkdir(parents=True, exist_ok=True)
        with audit.scope(output_dir / "openai_usage"):
            if on_stage:
                on_stage("astra_direct_start", {})
            result = await asyncio.to_thread(_generate_direct, raw_text, category=category,
                                            version=version, output_dir=output_dir)
            if on_stage:
                on_stage("astra_direct_done", {"chars": len(result)})
            return result
    if settings.OPENAI_WRITER_STREAM:
        raise BatchBudgetStop("2610智能体需非流式主笔以完整记录用量")
    output_dir.mkdir(parents=True, exist_ok=True)
    with audit.scope(output_dir / "openai_usage"):
        manuscript = await classic_text_workflow.generate_full_book(
            raw_text, category=agent["base_category"], output_dir=output_dir, on_stage=on_stage)
        if on_stage:
            on_stage("astra_start", {})
        result = await asyncio.to_thread(_rewrite, manuscript, output_dir,
            rendered_prompt=render_prompt(manuscript, workflow_version=version), workflow_version=version)
        if on_stage:
            on_stage("astra_done", {"chars": len(result)})
        return result
