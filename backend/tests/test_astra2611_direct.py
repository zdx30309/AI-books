import asyncio
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from io import BytesIO
from zipfile import ZipFile
from xml.etree import ElementTree as ET
import sys
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.prompts.astra_agents2610 import AGENTS, render_prompt, DIRECT_VERSIONS
from app.services import astra_agents2610_workflow as workflow, content_engine
from app.services.batch_spend import BatchBudgetStop
from app.api.routers import tasks
from app.prompts.classic_text import workflow_for_existing_task

CATEGORY='social_pain_2611_astra_direct'
VERSION=AGENTS[CATEGORY]['version']
BOOK='#最终书名\n新的书名\n#最终简介\n简介\n#目录\n#1. 起点\n#导言\n导言\n#正文\n#1. 起点\n完整正文。'

@pytest.fixture(autouse=True,params=['social_pain_2611_astra_direct','general_manuscript'])
def selected_agent(request,monkeypatch):
    monkeypatch.setitem(globals(),'CATEGORY',request.param)
    monkeypatch.setitem(globals(),'VERSION',AGENTS[request.param]['version'])


def install(monkeypatch,calls,mode='completed'):
    def create(**kw):
        calls.append(kw)
        if mode=='timeout':raise TimeoutError()
        return SimpleNamespace(id='offline',model='gpt-6-astra',status='incomplete' if mode=='incomplete' else 'completed',output_text='' if mode=='empty' else BOOK,usage=None)
    class Client:
        responses=SimpleNamespace(create=create)
        def with_options(self,**kw):
            assert kw=={'max_retries':0}
            return self
    monkeypatch.setattr(content_engine,'_get_openai_writer_client',lambda:Client())
    monkeypatch.setattr(workflow,'distributed_semaphore',lambda *a,**kw:nullcontext())
    async def forbidden(*a,**kw):raise AssertionError('Direct must not call classic pipeline')
    monkeypatch.setattr(workflow.classic_text_workflow,'generate_full_book',forbidden)
    for name in ('polish_book_text','refine_book_text','format_book_text','_route_writer_call'):
        monkeypatch.setattr(content_engine,name,forbidden)

def run(path):
    return asyncio.run(content_engine.generate_full_book('视频{原稿}',category=CATEGORY,text_workflow_version=VERSION,output_dir=path,style_reference='不追加文风'))

def test_standard_high_exact_prompt_single_call_recovery_and_delivery(monkeypatch,tmp_path):
    calls=[];install(monkeypatch,calls)
    cfg=tasks._normalize_task_config({'category':CATEGORY,'bookshelf_version':'2.0'})
    assert cfg['category_label']==AGENTS[CATEGORY]['label'] and cfg['text_workflow_version']==VERSION
    assert workflow_for_existing_task(cfg) in DIRECT_VERSIONS
    assert run(tmp_path)==run(tmp_path)==BOOK and len(calls)==1
    assert calls[0]['model']=='gpt-6-astra' and calls[0]['reasoning']=={'mode':'standard','effort':'high'}
    assert calls[0]['input']==render_prompt('视频{原稿}',workflow_version=VERSION)
    assert '不追加文风' not in calls[0]['input'] and 'temperature' not in calls[0]
    (tmp_path/'01_writer.md').unlink();(tmp_path/'04_format.md').unlink()
    assert run(tmp_path)==BOOK and len(calls)==1
    assert (tmp_path/'01_writer.md').read_text()==(tmp_path/'04_format.md').read_text()==BOOK
    assert not any((tmp_path/n).exists() for n in ('02_polish.md','03_refine.md','05_astra.md'))
    task=SimpleNamespace(config={**cfg,'markdown':BOOK})
    assert tasks._read_task_markdown(None,task)==BOOK
    data=tasks._markdown_to_docx(BOOK,category=CATEGORY,text_workflow_version=VERSION)
    with ZipFile(BytesIO(data)) as z:
        root=ET.fromstring(z.read('word/document.xml'));ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
        assert [''.join(t.text or '' for t in p.findall('.//w:t',ns)) for p in root.findall('.//w:body/w:p',ns)]==BOOK.splitlines()
    with pytest.raises(BatchBudgetStop):
        asyncio.run(workflow.generate_full_book('更换素材',category=CATEGORY,workflow_version=VERSION,output_dir=tmp_path))
    assert len(calls)==1

@pytest.mark.parametrize('mode',['timeout','incomplete','empty'])
def test_failed_or_uncertain_direct_request_is_never_automatically_replayed(monkeypatch,tmp_path,mode):
    calls=[];install(monkeypatch,calls,mode)
    for _ in range(2):
        with pytest.raises(BatchBudgetStop):run(tmp_path)
    assert len(calls)==1 and not (tmp_path/'04_format.md').exists()

def test_direct_refuses_unversioned_old_stages(monkeypatch,tmp_path):
    calls=[];install(monkeypatch,calls)
    (tmp_path/'01_writer.md').write_text('旧稿')
    with pytest.raises(BatchBudgetStop):run(tmp_path)
    assert not calls
