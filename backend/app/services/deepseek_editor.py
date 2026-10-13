from app.core.config import settings
from app.prompts.goal_driven_templates import GEMINI_FAST_POLISH_PROMPT, GEMINI_FAST_REFINE_PROMPT
from app.services import deepseek
from app.services.gemini import GeminiMaxTokensError


def generate_text(prompt, *, system_instruction=None, temperature=None,
                  max_output_tokens=None, request_timeout_seconds=None, **kwargs):
    try:
        return deepseek.generate_text(
            prompt, model=settings.DEEPSEEK_WRITER_MODEL or settings.DEEPSEEK_MODEL,
            system_instruction=system_instruction, temperature=temperature,
            max_output_tokens=max_output_tokens, request_timeout_seconds=request_timeout_seconds,
            thinking=False, fail_on_max_tokens=True,
        )
    except deepseek.DeepSeekMaxTokensError as exc:
        raise GeminiMaxTokensError(str(exc), partial_text=exc.partial_text) from exc


def polish_book_text(text, *, system_instruction=None, **kwargs):
    if "max_output_tokens" not in kwargs:
        kwargs["max_output_tokens"] = min(32768, max(4096, len(text) * 2 + 1024))
    return generate_text(text, system_instruction=system_instruction or GEMINI_FAST_POLISH_PROMPT, **kwargs)


def refine_book_text(text, *, system_instruction=None, **kwargs):
    if "max_output_tokens" not in kwargs:
        kwargs["max_output_tokens"] = min(32768, max(4096, len(text) * 2 + 1024))
    return generate_text(text, system_instruction=system_instruction or GEMINI_FAST_REFINE_PROMPT, **kwargs)
