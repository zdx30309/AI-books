"""Task-scoped OpenAI receipts; successful responses replay locally, never pay twice."""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from app.services.batch_spend import BatchBudgetStop

_current = ContextVar('manuscript_openai_audit', default=None)
logger = logging.getLogger(__name__)

@contextmanager
def scope(directory):
    token = _current.set(Path(directory))
    try:
        yield
    finally:
        _current.reset(token)

def active():
    return _current.get() is not None

def attr(obj, name, default=None):
    return obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)

def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.chmod(0o600)
    tmp.replace(path)

def create(client, kwargs, action='classic_writer'):
    from app.services.content_engine import _extract_openai_response_text
    directory = _current.get()
    if directory is None:
        raise RuntimeError('OpenAI audit scope required')
    kwargs = {**kwargs, 'service_tier': 'default'}
    digest = hashlib.sha256(json.dumps(kwargs, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    path = directory / (action + '-' + digest + '.json')
    if path.exists():
        record = json.loads(path.read_text())
        if record.get('status') != 'completed' or not record.get('text', '').strip():
            raise BatchBudgetStop('已有未完成或不确定的OpenAI请求，暂停核对，避免重复扣费')
        return SimpleNamespace(id=record['response_id'], model=record['response_model'], status='completed',
                               output_text=record['text'], usage=SimpleNamespace(**record['usage']))
    record = {'action': action, 'model': kwargs['model'], 'request_sha256': digest,
              'reasoning': kwargs.get('reasoning'), 'status': 'request_started',
              'created_at': datetime.now(timezone.utc).isoformat(), 'cost_usd_estimate': None}
    save(path, record)
    try:
        response = client.with_options(max_retries=0).responses.create(**kwargs)
        usage = attr(response, 'usage')
        details = attr(usage, 'input_tokens_details')
        counts = {'input_tokens': attr(usage, 'input_tokens'), 'output_tokens': attr(usage, 'output_tokens'),
                  'cached_tokens': attr(details, 'cached_tokens'),
                  'cache_write_tokens': attr(details, 'cache_write_tokens'),
                  'reasoning_tokens': attr(attr(usage, 'output_tokens_details'), 'reasoning_tokens')}
        record.update(status=attr(response, 'status', 'unknown'), response_id=attr(response, 'id'),
                      response_model=attr(response, 'model'), usage=counts,
                      text=attr(response, 'output_text') or _extract_openai_response_text(response))
        # A transparent standard-rate estimate; detailed provider billing may differ.
        rates = {'gpt-6-astra': (10.0, 1.0, 12.5, 50.0), 'gpt-6.1-sol': (2.0, .1, 2.5, 10.0)}
        if kwargs['model'] in rates and all(type(counts[k]) is int for k in ('input_tokens', 'output_tokens')):
            inp, out = counts['input_tokens'], counts['output_tokens']
            ir, cr, wr, outr = rates[kwargs['model']]
            if inp > 272000:
                ir *= 2; cr *= 2; wr *= 2; outr *= 1.5
            cached = counts['cached_tokens'] or 0
            writes = counts['cache_write_tokens'] or 0
            record['cost_usd_estimate'] = ((inp-cached-writes)*ir + cached*cr + writes*wr + out*outr)/1_000_000
            record['cost_basis'] = 'Standard USD token rates; reported cache counts; not an invoice'
        save(path, record)
        logger.info('OpenAI action=%s model=%s status=%s cost_usd_estimate=%s', action, kwargs['model'], record['status'], record['cost_usd_estimate'])
        if record['status'] != 'completed' or not record['text'].strip():
            raise BatchBudgetStop('OpenAI未完整返回书稿，已保存响应，暂停且不自动重放')
        return response
    except BatchBudgetStop:
        raise
    except Exception as error:
        if record.get('status') != 'completed':
            record.update(status='failed_or_uncertain', error_type=type(error).__name__, error_code=getattr(error, 'status_code', None))
            save(path, record)
        logger.exception('OpenAI action=%s model=%s status=%s cost_usd_estimate=%s', action, kwargs['model'], record['status'], record['cost_usd_estimate'])
        raise BatchBudgetStop('OpenAI请求或保存失败，保留记录暂停，避免重复扣费') from error
