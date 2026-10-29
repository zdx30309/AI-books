import asyncio
from pathlib import Path
from types import SimpleNamespace
from contextlib import nullcontext
from zipfile import ZipFile
from io import BytesIO
from xml.etree import ElementTree as ET
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.prompts.astra_agents2610 import AGENTS, PROMPT_PATH, PROMPT_PATHS, VERSION_CATEGORIES, render_prompt
from app.prompts.classic_text import base_category, get_prompts, workflow_for_existing_task
from app.services import astra_agents2610_workflow as workflow, social_pain2610_workflow as shared, content_engine
from app.api.routers import tasks
from app.services.bookshelf_versions import task_shelf_version

@pytest.mark.parametrize('pinned_version,category', VERSION_CATEGORIES.items())
def test_each_agent_pins_and_routes_correct_subject(category,pinned_version,tmp_path,monkeypatch):
    agent=AGENTS[category]
    cfg=tasks._normalize_task_config({'category':category,'bookshelf_version':'2.0'})
    assert cfg['text_workflow_version']==agent['version']
    assert cfg['text_prompt_base_category']==base_category(agent['base_category'])
    assert task_shelf_version(cfg)=='2.0'
    assert workflow_for_existing_task(cfg)==agent['version']
    assert workflow_for_existing_task({**cfg,'text_workflow_version':pinned_version})==pinned_version
    for other in AGENTS:
        if other!=category:
            with pytest.raises(ValueError):workflow_for_existing_task({**cfg,'category':other})
    calls=[]
    manuscript='@最终书名\n初稿\n@正文\n原类别完整文稿'
    async def classic(raw_text, **kw):
        assert raw_text=='视频原文' and kw['category']==agent['base_category']
        assert get_prompts(kw['category'])==get_prompts(category)
        (tmp_path/'04_format.md').write_text(manuscript)
        calls.append('classic')
        return manuscript
    final='#最终书名\n终稿\n#最终简介\n简介\n#目录\n#1. 起点\n#导言\n导言\n#正文\n#1. 起点\n正文'
    def create(**kw):
        assert kw['model']=='gpt-6-astra'
        assert kw['reasoning']=={'mode':'standard','effort':'high'}
        assert kw['input']==render_prompt(manuscript,workflow_version=pinned_version)
        calls.append('astra')
        return SimpleNamespace(id='offline',model='gpt-6-astra',status='completed',output_text=final,usage=None)
    client=SimpleNamespace(responses=SimpleNamespace(create=create))
    client.with_options=lambda **kw:client
    monkeypatch.setattr(workflow.classic_text_workflow,'generate_full_book',classic)
    monkeypatch.setattr(shared,'distributed_semaphore',lambda *a,**kw:nullcontext())
    monkeypatch.setattr(content_engine,'_get_openai_writer_client',lambda:client)
    monkeypatch.setattr(workflow.settings,'OPENAI_WRITER_STREAM',False)
    for _ in range(2):
        assert asyncio.run(content_engine.generate_full_book('视频原文',category=category,text_workflow_version=pinned_version,output_dir=tmp_path))==final
    assert calls.count('astra')==1
    assert (tmp_path/'05_astra.md').read_text()==final
    data=tasks._markdown_to_docx(final,category=category,text_workflow_version=agent['version'])
    with ZipFile(BytesIO(data)) as z:
        root=ET.fromstring(z.read('word/document.xml'))
        ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
        paragraphs=[''.join(t.text or '' for t in p.findall('.//w:t',ns)) for p in root.findall('.//w:body/w:p',ns)]
        assert paragraphs==final.splitlines()


def test_template_is_frozen_word_text():
    import hashlib
    assert PROMPT_PATH.read_text().count('{raw_text}')==1
    assert hashlib.sha256(PROMPT_PATH.read_bytes()).hexdigest()== 'c83390f822efb3e756f56c9e46eb591665872910a94a3007778487eca66d9c26'


def test_previous_prompt_remains_frozen():
    import hashlib
    from app.prompts.astra_agents2610 import PREVIOUS_PROMPT_PATH
    assert hashlib.sha256(PREVIOUS_PROMPT_PATH.read_bytes()).hexdigest()=='0a36cdadf88ffaa980fadd7c5656f4bb9b1d2ba6e492f05f27552e769d92f6cb'
    assert render_prompt('底稿',workflow_version='finance2610_classic_astra_20261027') != render_prompt('底稿')


def test_prompt_upgrade_is_scoped_and_historical_versions_stay_frozen():
    import hashlib
    assert hashlib.sha256(PROMPT_PATHS['20261028'].read_bytes()).hexdigest()=='c3b174ba519917cd0a187da71ad7a95382a1fece8987c3283913e4477ebf593c'
    assert AGENTS['finance_technology_2610']['version'].endswith('20261028')
    assert render_prompt('材料',workflow_version=AGENTS['finance_technology_2610']['version'])==PROMPT_PATHS['20261028'].read_text().replace('{raw_text}','材料')
    assert 'social_pain2610_2_classic_astra_20261028' not in VERSION_CATEGORIES
    from app.prompts.social_pain2610 import VERSION
    assert VERSION=='social_pain2610_classic_astra_20261017'
