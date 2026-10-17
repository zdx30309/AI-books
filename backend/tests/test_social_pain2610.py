import asyncio
import json
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.prompts.social_pain2610 import CATEGORY, VERSION, MODEL, PROMPT_PATH
from app.services import social_pain2610_workflow as workflow, openai_manuscript_audit as audit, content_engine
from app.services.batch_spend import BatchBudgetStop
from app.api.routers import tasks
from app.prompts.classic_text import workflow_for_existing_task

def test_category_isolation():
    cfg = tasks._normalize_task_config({'category':CATEGORY,'bookshelf_version':'2.0'})
    assert cfg['text_workflow_version']==VERSION
    assert cfg['text_prompt_base_category']=='social_science'
    assert workflow_for_existing_task(cfg)==VERSION
    with pytest.raises(ValueError):
        workflow_for_existing_task({'category':'social_science','text_workflow_version':VERSION})

def client_response(text='@最终书名\n测试书\n@正文\n正文'):
    client=Mock()
    client.with_options.return_value=client
    client.responses.create.return_value=SimpleNamespace(id='response',model=MODEL,status='completed',output_text=text,
        usage=SimpleNamespace(input_tokens=1000, output_tokens=2000,input_tokens_details=SimpleNamespace(cached_tokens=100),output_tokens_details=SimpleNamespace(reasoning_tokens=300)))
    return client

def test_five_stages_final_exact_and_receipt_replay(tmp_path,monkeypatch):
    manuscript='@最终书名\n旧书\n@正文\n旧流程完整成稿'
    async def classic(raw_text,**kw):
        assert raw_text=='视频原文' and kw['category']=='social_science'
        assert audit.active()
        (tmp_path/'04_format.md').write_text(manuscript)
        return manuscript
    monkeypatch.setattr(workflow.classic_text_workflow,'generate_full_book',classic)
    monkeypatch.setattr(workflow.settings,'OPENAI_WRITER_STREAM',False)
    monkeypatch.setattr(workflow,'distributed_semaphore',lambda *a,**k:nullcontext())
    client=client_response()
    monkeypatch.setattr(content_engine,'_get_openai_writer_client',lambda:client)
    for _ in range(2):
        result=asyncio.run(workflow.generate_full_book('视频原文',output_dir=tmp_path))
        assert result==(tmp_path/'05_astra.md').read_text()==client.responses.create.return_value.output_text
        assert (tmp_path/'04_format.md').read_text()==manuscript
    assert client.responses.create.call_count==1
    kwargs=client.responses.create.call_args.kwargs
    assert kwargs['model']==MODEL and kwargs['reasoning']['mode']=='standard'
    assert kwargs['input']==PROMPT_PATH.read_text().replace('{raw_text}',manuscript)
    receipt=json.loads(next((tmp_path/'openai_usage').glob('*.json')).read_text())
    assert receipt['usage']['reasoning_tokens']==300
    assert receipt['cost_usd_estimate']==pytest.approx(.1091)

def test_uncertain_paid_call_never_replayed(tmp_path):
    client=client_response();client.responses.create.side_effect=TimeoutError()
    with audit.scope(tmp_path):
        for _ in range(2):
            with pytest.raises(BatchBudgetStop):audit.create(client,{'model':MODEL,'input':'test'})
    assert client.responses.create.call_count==1

def test_classic_writer_is_audited_only_in_scope(tmp_path,monkeypatch):
    client=client_response();monkeypatch.setattr(content_engine,'_get_openai_writer_client',lambda:client)
    with audit.scope(tmp_path):
        for _ in range(2):content_engine._create_openai_writer_response('test',effort='xhigh',verb='high',max_tok=None)
    assert client.responses.create.call_count==1
    assert client.responses.create.call_args.kwargs['model']==content_engine.settings.OPENAI_WRITER_MODEL

@pytest.mark.parametrize('model,inputs,outputs,cached,writes,expected', [
    ('gpt-6.1-sol',3243,14861,0,3240,.156716),
    ('gpt-6.1-sol',1000,2000,100,0,.02181),
    ('gpt-6-astra',1000,2000,100,500,.11035),
])
def test_usage_cost_includes_cache_writes_without_double_counting_reasoning(tmp_path,model,inputs,outputs,cached,writes,expected):
    client=client_response()
    client.responses.create.return_value.usage=SimpleNamespace(input_tokens=inputs,output_tokens=outputs,
        input_tokens_details=SimpleNamespace(cached_tokens=cached,cache_write_tokens=writes),
        output_tokens_details=SimpleNamespace(reasoning_tokens=1000))
    with audit.scope(tmp_path):audit.create(client,{'model':model,'input':'cost test'})
    receipt=json.loads(next(tmp_path.glob('*.json')).read_text())
    assert receipt['cost_usd_estimate']==pytest.approx(expected)
