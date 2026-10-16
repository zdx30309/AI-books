"""One paid Astra Pro response; deliver its text without editorial passes."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path

from app.core.config import settings
from app.prompts.sheke2610 import MODEL, REASONING_MODE, VERSION, render_prompt
from app.services.batch_spend import BatchBudgetStop
from app.utils.distributed_limiter import distributed_semaphore

logger = logging.getLogger(__name__)


def _save(path: Path, value: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)


def _generate(raw_text: str, output_dir: Path) -> str:
    from app.services import content_engine as engine

    prompt = render_prompt(raw_text)
    identity = {"version": VERSION, "model": MODEL, "reasoning_mode": REASONING_MODE,
                "reasoning_effort": "xhigh", "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest()}
    manifest_path = output_dir / "00_text_workflow.json"
    receipt_path = output_dir / "01_direct_response.json"
    final_path = output_dir / "04_format.md"
    if manifest_path.exists():
        if json.loads(manifest_path.read_text()) != identity:
            raise BatchBudgetStop("社科2610缓存与当前提示词或素材不同，请明确重新生成")
    else:
        if any(output_dir.glob("0[1-4]_*.md")) or receipt_path.exists():
            raise BatchBudgetStop("社科2610拒绝混用旧流程产物，请明确重新生成")
        _save(manifest_path, json.dumps(identity, ensure_ascii=False, indent=2))

    if receipt_path.exists():
        receipt = json.loads(receipt_path.read_text())
        if receipt.get("status") == "completed" and receipt.get("text", "").strip():
            # Recover a successful paid response even if the worker died before stage writes.
            result = receipt["text"]
            if not final_path.exists():
                _save(final_path, result)
            if not (output_dir / "01_writer.md").exists():
                _save(output_dir / "01_writer.md", result)
            return result
        raise BatchBudgetStop("社科2610已有未完成或不确定的付费请求，已暂停，避免自动重复扣费；请核对响应记录")

    with distributed_semaphore("openai-writer", limit=settings.OPENAI_WRITER_MAX_CONCURRENT_CALLS):
        receipt = {**identity, "action": "sheke2610_direct", "status": "request_started",
                   "created_at": datetime.now(timezone.utc).isoformat(), "cost_usd_estimate": None}
        _save(receipt_path, json.dumps(receipt, ensure_ascii=False, indent=2))
        try:
            # Explicit model/mode: do not change any other category's global model.
            response = engine._get_openai_writer_client().with_options(max_retries=0).responses.create(
                model=MODEL, input=prompt,
                reasoning={"mode": REASONING_MODE, "effort": "xhigh"},
                max_output_tokens=65536, service_tier="default",
            )
            attr = engine._response_attr
            usage = attr(response, "usage")
            input_tokens, output_tokens = attr(usage, "input_tokens"), attr(usage, "output_tokens")
            # Official standard rates. Cache discount not deducted; this is an estimate, not an invoice.
            cost = ((input_tokens * 10 + output_tokens * 50) / 1_000_000
                    if type(input_tokens) is int and type(output_tokens) is int else None)
            raw_output = attr(response, "output_text")
            result = raw_output if isinstance(raw_output, str) else engine._extract_openai_response_text(response)
            receipt.update(response_id=attr(response, "id"), response_model=attr(response, "model"),
                           status=attr(response, "status", "unknown"), text=result,
                           input_tokens=input_tokens, output_tokens=output_tokens,
                           cost_usd_estimate=cost, cost_basis="standard rates, cache discount not deducted",
                           incomplete_details=str(attr(response, "incomplete_details") or ""))
            _save(receipt_path, json.dumps(receipt, ensure_ascii=False, indent=2))
            logger.info("sheke2610 model=%s mode=%s status=%s input_tokens=%s output_tokens=%s cost_usd_estimate=%s",
                        MODEL, REASONING_MODE, receipt["status"], input_tokens, output_tokens, cost)
            if receipt["status"] != "completed" or not result.strip():
                raise BatchBudgetStop("Astra Pro未返回完整书稿，响应已保存，未自动续写或润色")
            _save(output_dir / "01_writer.md", result)
            _save(final_path, result)
            return result
        except BatchBudgetStop:
            raise
        except Exception as error:
            # Preserve completed response on a filesystem failure; otherwise retain an uncertain request.
            if receipt.get("status") != "completed":
                receipt.update(status="failed_or_uncertain", error_type=type(error).__name__,
                               error_code=getattr(error, "status_code", None))
                _save(receipt_path, json.dumps(receipt, ensure_ascii=False, indent=2))
            logger.exception("sheke2610 model=%s mode=%s action=direct status=%s cost_usd_estimate=%s",
                             MODEL, REASONING_MODE, receipt["status"], receipt.get("cost_usd_estimate"))
            raise BatchBudgetStop("Astra Pro请求或结果保存失败，已暂停并保留记录，不自动重放") from error


async def generate_full_book(raw_text: str, *, output_dir: Path | None = None, on_stage=None) -> str:
    if output_dir is None:
        raise ValueError("社科2610需要独立输出目录保存付费请求与断点")
    output_dir.mkdir(parents=True, exist_ok=True)
    if on_stage:
        on_stage("direct_start", {})
    result = await asyncio.to_thread(_generate, raw_text, output_dir)
    if on_stage:
        on_stage("direct_done", {"chars": len(result)})
    return result
