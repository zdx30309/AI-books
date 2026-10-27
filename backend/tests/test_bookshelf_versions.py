from pathlib import Path
import socket
import sys
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import JSON,MetaData,create_engine
from sqlalchemy.orm import sessionmaker
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.api.routers import tasks as router
from app.models import Task,Chapter,User
from app.prompts.classic_text import VERSION as CLASSIC
from app.prompts.sheke2610 import VERSION as DIRECT
from app.schemas.task import BatchCreateTaskRequest,TaskMetadataUpdateRequest,TaskStatusResponse
from app.services.bookshelf_versions import task_shelf_version,shelf_version_expression


@pytest.fixture
def database(tmp_path,monkeypatch):
    def no_network(*args,**kwargs):raise AssertionError('offline tests')
    monkeypatch.setattr(socket.socket,'connect',no_network)
    engine=create_engine('sqlite://')
    meta=MetaData()
    for model in (User,Task,Chapter):
        table=model.__table__.to_metadata(meta)
        if model is Task:table.c.config.type=JSON()
    meta.create_all(engine)
    factory=sessionmaker(bind=engine,expire_on_commit=False)
    monkeypatch.setattr(router,'_task_list_config_expr',lambda:Task.config.label('config'))
    monkeypatch.setattr(router,'_ensure_generation_capacity',lambda *a,**k:None)
    monkeypatch.setattr(router,'_dispatch_task',lambda *a,**k:None)
    monkeypatch.setattr(router,'PROJECT_ROOT',tmp_path)
    with factory() as db:
        owner=User(id=uuid.uuid4(),password_hash='offline');other=User(id=uuid.uuid4(),password_hash='offline')
        db.add_all([owner,other]);db.commit()
        yield db,owner,other
    engine.dispose()


def add_task(db,user,config,url='https://www.bilibili.com/video/BV1F4m2YZER3/'):
    task=Task(id=uuid.uuid4(),owner_id=user.id,source_url=url,status='completed',progress=100,config=config)
    db.add(task);db.commit();return task


def test_legacy_inference_new_creation_and_category_options():
    assert task_shelf_version({})=='1.0'
    assert task_shelf_version({'category':'social_science_test'})=='1.0'
    assert task_shelf_version({'text_workflow_version':CLASSIC})=='1.0'
    assert task_shelf_version({'text_workflow_version':DIRECT})=='2.0'
    assert router._normalize_task_config({'category':'social_science_test'})['bookshelf_version']=='2.0'
    assert router._normalize_task_config({'category':'social_science'})['bookshelf_version']=='1.0'
    assert {c.id for c in router.get_task_categories('2.0').items}=={'social_science_test', 'social_pain_2610', 'finance_technology_2610', 'psychology_emotion_2610', 'health_wellness_2610'}
    assert 'social_science_test' not in {c.id for c in router.get_task_categories('1.0').items}
    with pytest.raises(HTTPException) as error:
        router._normalize_task_config({'category':'social_science_test','bookshelf_version':'1.0'})
    assert error.value.status_code==400
    with pytest.raises(HTTPException):
        router._normalize_task_config({'category':'social_science','bookshelf_version':'2.0'})


def test_list_versions_partition_before_pagination_and_keep_owner(database):
    db,owner,other=database
    a=add_task(db,owner,{'category':'social_science_test','text_workflow_version':CLASSIC})
    b=add_task(db,owner,{'category':'social_science_test','text_workflow_version':DIRECT})
    c=add_task(db,owner,{'category':'social_science','bookshelf_version':'1.0'})
    add_task(db,other,{'category':'social_science_test','text_workflow_version':DIRECT})
    v1=router.list_tasks(page=1,page_size=1,status_filter=None,bookshelf_version='1.0',db=db,user=owner)
    v2=router.list_tasks(page=1,page_size=1,status_filter=None,bookshelf_version='2.0',db=db,user=owner)
    assert v1.total==2 and len(v1.items)==1 and v1.items[0].bookshelf_version=='1.0'
    assert v2.total==1 and v2.items[0].id==b.id and v2.items[0].bookshelf_version=='2.0'
    all_v1=router.list_tasks(page=1,page_size=0,status_filter=None,bookshelf_version='1.0',db=db,user=owner)
    assert {item.id for item in all_v1.items}=={a.id,c.id}
    assert TaskStatusResponse.model_validate(b).bookshelf_version=='2.0'
    assert router._build_list_item(db,b).bookshelf_version=='2.0'


def test_candidate_dedup_separates_v1_from_v2(database):
    db,owner,_=database
    url='https://www.bilibili.com/video/BV1F4m2YZER3/'
    add_task(db,owner,{'category':'social_science','text_workflow_version':CLASSIC},url)
    req=BatchCreateTaskRequest(urls=[url],config={'category':'social_science_test','bookshelf_version':'2.0'})
    first=router.create_task_batch(req,db=db,user=owner)
    assert first.total_created==1
    task=db.get(Task,first.created[0].task_id)
    assert task.config['bookshelf_version']=='2.0' and task.config['text_workflow_version']==DIRECT
    assert router.create_task_batch(req,db=db,user=owner).total_created==0


def test_relabel_cannot_change_chain_but_regeneration_pins_target(database):
    db,owner,_=database
    old=add_task(db,owner,{'category':'social_science','text_workflow_version':CLASSIC,'markdown':'old'})
    with pytest.raises(HTTPException):
        router.update_task_metadata(old.id,TaskMetadataUpdateRequest(category='social_science_test'),db=db,user=owner)
    assert old.config['markdown']=='old'
    direct=add_task(db,owner,{'category':'social_science_test','text_workflow_version':DIRECT})
    with pytest.raises(HTTPException):
        router.update_task_metadata(direct.id,TaskMetadataUpdateRequest(category=''),db=db,user=owner)
    legacy=add_task(db,owner,{'category':'social_science_test','bookshelf_version':'1.0','text_workflow_version':CLASSIC})
    router._prepare_task_for_regeneration(db,legacy)
    assert legacy.config['bookshelf_version']=='2.0' and legacy.config['text_workflow_version']==DIRECT


def test_v2_agents_cannot_relabel_each_other(database):
    db,owner,_=database
    direct=add_task(db,owner,{'category':'social_science_test','text_workflow_version':DIRECT})
    with pytest.raises(HTTPException):
        router.update_task_metadata(direct.id,TaskMetadataUpdateRequest(category='social_pain_2610'),db=db,user=owner)


@pytest.mark.parametrize("category", ["finance_technology_2610", "psychology_emotion_2610", "health_wellness_2610"])
def test_new_agents_preserve_final_and_parse_hash_chapters(database,category):
    from app.prompts.astra_agents2610 import AGENTS
    from app.tasks.tasks import _persist_chapters_from_markdown
    db,owner,_=database
    config=router._normalize_task_config({"category":category})
    final="#最终书名\n终稿\n#最终简介\n简介\n#目录\n#1. 起点\n#2. 后续\n#导言\n导言\n#正文\n#1. 起点\n甲章正文\n#2. 后续\n乙章正文"
    task=add_task(db,owner,{**config,"markdown":final})
    assert _persist_chapters_from_markdown(db,task,final)==2
    assert router._read_task_markdown(db,task)==final
    with pytest.raises(HTTPException):
        router.update_task_metadata(task.id,TaskMetadataUpdateRequest(category="social_pain_2610"),db=db,user=owner)
