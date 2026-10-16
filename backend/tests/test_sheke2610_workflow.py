import asyncio
from contextlib import nullcontext
from io import BytesIO
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from zipfile import ZipFile

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.api.routers.tasks import _normalize_task_config, _markdown_to_docx, _read_task_markdown
from app.prompts.classic_text import VERSION as CLASSIC, workflow_for_existing_task
from app.prompts.sheke2610 import MODEL, VERSION, render_prompt
from app.services import content_engine, sheke2610_workflow as direct
from app.services.batch_spend import BatchBudgetStop
from app.tasks.tasks import _extract_book_title

BOOK = '#最终书名\n具体的书名\n#最终简介\n简介\n#目录\n#1. 一个问题\n#导言\n导言\n#正文\n#1. 一个问题\n保留原文，不做润色。'


def install(monkeypatch, calls, mode='completed'):
    def create(**kwargs):
        calls.append(kwargs)
        if mode == 'timeout':
            raise TimeoutError()
        return SimpleNamespace(status='incomplete' if mode == 'incomplete' else 'completed',
            output_text='' if mode == 'empty' else BOOK, model=MODEL, id='resp_test',
            usage=SimpleNamespace(input_tokens=10, output_tokens=20))
    class Client:
        responses = SimpleNamespace(create=create)
        def with_options(self, **kwargs):
            assert kwargs == {'max_retries': 0}
            return self
    monkeypatch.setattr(content_engine, '_get_openai_writer_client', lambda: Client())
    monkeypatch.setattr(direct, 'distributed_semaphore', lambda *a, **kw: nullcontext())
    def forbid(*args, **kwargs):
        raise AssertionError('No additional writer, polish, refine, formatting or repair')
    for name in ('polish_book_text', 'refine_book_text', 'format_book_text', '_route_writer_call'):
        monkeypatch.setattr(content_engine, name, forbid)


def test_new_direct_and_old_pinned_workflows():
    config = _normalize_task_config({'category': 'social_science_test'})
    assert config['text_workflow_version'] == VERSION
    assert config['category_label'] == '社科2610'
    assert _normalize_task_config({'category': 'social_science'})['text_workflow_version'] == CLASSIC
    assert workflow_for_existing_task({'category': 'social_science_test'}) == 'legacy_20260704'
    assert workflow_for_existing_task({'category': 'social_science_test', 'text_workflow_version': CLASSIC}) == CLASSIC
    with pytest.raises(ValueError):
        workflow_for_existing_task({'category': 'popular_science', 'text_workflow_version': VERSION})


def test_single_pro_exact_prompt_and_cache(monkeypatch, tmp_path):
    calls = []
    install(monkeypatch, calls)
    async def run():
        return await content_engine.generate_full_book('{素材}', category='social_science_test',
            text_workflow_version=VERSION, output_dir=tmp_path, style_reference='绝不能加入的文风')
    assert asyncio.run(run()) == BOOK
    assert asyncio.run(run()) == BOOK
    assert len(calls) == 1
    request = calls[0]
    assert request['model'] == MODEL
    assert request['reasoning'] == {'mode': 'pro', 'effort': 'xhigh'}
    assert request['input'] == render_prompt('{素材}')
    assert '绝不能加入的文风' not in request['input']
    assert 'stream' not in request and 'temperature' not in request
    assert (tmp_path / '04_format.md').read_text() == BOOK
    assert not (tmp_path / '02_polish.md').exists()
    assert not (tmp_path / '03_refine.md').exists()
    (tmp_path / '04_format.md').unlink()
    assert asyncio.run(run()) == BOOK and len(calls) == 1
    assert json.loads((tmp_path / '01_direct_response.json').read_text())['cost_usd_estimate'] == pytest.approx(0.0011)
    with pytest.raises(BatchBudgetStop, match='缓存'):
        asyncio.run(direct.generate_full_book('changed', output_dir=tmp_path))


@pytest.mark.parametrize('mode', ['timeout', 'incomplete', 'empty'])
def test_uncertain_request_not_replayed(monkeypatch, tmp_path, mode):
    calls = []
    install(monkeypatch, calls, mode)
    for _ in range(2):
        with pytest.raises(BatchBudgetStop):
            asyncio.run(direct.generate_full_book('素材', output_dir=tmp_path))
    assert len(calls) == 1
    assert not (tmp_path / '04_format.md').exists()


def test_preview_and_export_preserve_hash_markers():
    from xml.etree import ElementTree as etree
    task = SimpleNamespace(config={'category': 'social_science_test', 'text_workflow_version': VERSION, 'markdown': BOOK})
    assert _extract_book_title(BOOK) == '具体的书名'
    assert _read_task_markdown(None, task) == BOOK
    content = _markdown_to_docx(BOOK, category='social_science_test', text_workflow_version=VERSION)
    with ZipFile(BytesIO(content)) as archive:
        tree = etree.fromstring(archive.read('word/document.xml'))
        ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
        text = '\n'.join(''.join(n.text or '' for n in p.findall('.//w:t', ns)) for p in tree.findall('.//w:p', ns))
        assert text == BOOK
