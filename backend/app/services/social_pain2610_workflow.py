"""Classic four-stage manuscript followed by one user-template Astra edit."""
import asyncio
import hashlib
import json
from pathlib import Path
from app.prompts.social_pain2610 import MODEL, VERSION, render_prompt
from app.services import classic_text_workflow, openai_manuscript_audit as audit
from app.services.batch_spend import BatchBudgetStop
from app.core.config import settings
from app.utils.distributed_limiter import distributed_semaphore

def _rewrite(manuscript, output_dir):
    from app.services import content_engine as engine
    prompt = render_prompt(manuscript)
    identity = {'version': VERSION, 'prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest(),
                'model': MODEL, 'reasoning_mode': 'standard', 'reasoning_effort': 'high'}
    manifest = output_dir / '05_astra_manifest.json'
    if manifest.exists() and json.loads(manifest.read_text()) != identity:
        raise BatchBudgetStop('Astra润色素材或提示词已变化，请明确重新生成')
    audit.save(manifest, identity)
    with distributed_semaphore('openai-writer', limit=settings.OPENAI_WRITER_MAX_CONCURRENT_CALLS):
        response = audit.create(engine._get_openai_writer_client(), {'model': MODEL, 'input': prompt,
            'reasoning': {'mode': 'standard', 'effort': 'high'}, 'max_output_tokens': 65536}, action='astra_rewrite')
    text = audit.attr(response, 'output_text') or engine._extract_openai_response_text(response)
    classic_text_workflow._dump_stage(output_dir, '05_astra.md', text)
    return text

async def generate_full_book(raw_text, *, output_dir: Path | None = None, on_stage=None):
    if output_dir is None:
        raise ValueError('社会痛点2610需要独立输出目录')
    output_dir.mkdir(parents=True, exist_ok=True)
    if settings.OPENAI_WRITER_STREAM:
        raise BatchBudgetStop('社会痛点2610需非流式主笔以完整记录用量')
    with audit.scope(output_dir / 'openai_usage'):
        manuscript = await classic_text_workflow.generate_full_book(raw_text, category='social_science', output_dir=output_dir, on_stage=on_stage)
        if on_stage:
            on_stage('astra_start', {})
        result = await asyncio.to_thread(_rewrite, manuscript, output_dir)
        if on_stage:
            on_stage('astra_done', {'chars': len(result)})
        return result
