"""The existing 1.0 subject pipeline followed by one Astra rewrite."""
import asyncio
from pathlib import Path
from app.core.config import settings
from app.prompts.astra_agents2610 import AGENTS, render_prompt
from app.services import classic_text_workflow, openai_manuscript_audit as audit
from app.services.social_pain2610_workflow import _rewrite
from app.services.batch_spend import BatchBudgetStop

async def generate_full_book(raw_text, *, category, output_dir: Path | None = None, on_stage=None):
    agent = AGENTS[category]
    if output_dir is None:
        raise ValueError("2610智能体需要独立输出目录")
    if settings.OPENAI_WRITER_STREAM:
        raise BatchBudgetStop("2610智能体需非流式主笔以完整记录用量")
    output_dir.mkdir(parents=True, exist_ok=True)
    with audit.scope(output_dir / "openai_usage"):
        manuscript = await classic_text_workflow.generate_full_book(
            raw_text, category=agent["base_category"], output_dir=output_dir, on_stage=on_stage)
        if on_stage:
            on_stage("astra_start", {})
        result = await asyncio.to_thread(_rewrite, manuscript, output_dir,
            rendered_prompt=render_prompt(manuscript), workflow_version=agent["version"])
        if on_stage:
            on_stage("astra_done", {"chars": len(result)})
        return result
