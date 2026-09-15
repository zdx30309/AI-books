"""Owner-scoped controls for the general-longform Task category."""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.routers.auth import get_current_user
from app.db.session import get_db
from app.models import Task, TaskStatus, User
from app.prompts.general_longform_prompts import CATEGORY, available_versions, get_manifest
from app.services.general_longform import initial_state, load_state, public_state, work_dir
from app.services.general_longform_documents import inspect_docx
from app.services.general_longform_runtime import atomic_json, atomic_text, now_iso, read_json
from app.utils.docx_text import extract_docx_text

router = APIRouter(prefix="/api/v1/general-longform", tags=["GeneralLongform"])
BUSY = {TaskStatus.PENDING, TaskStatus.PROCESSING, TaskStatus.FETCHING_TOPICS, TaskStatus.FILTERING_TOPICS, TaskStatus.GENERATING_OUTLINE, TaskStatus.WRITING_CHAPTERS, TaskStatus.FACT_CHECKING}


class ActionRequest(BaseModel):
    action: Literal["plan", "revise_outline", "confirm_outline", "next_chapter", "revise_chapter", "assemble", "import_materials", "resume"]
    feedback: str = Field(default="", max_length=10000)
    chapter_number: int | None = Field(default=None, ge=1, le=15)
    packaging_index: int | None = Field(default=None, ge=0, le=4)
    outline_version: int | None = Field(default=None, ge=0)
    retry_uncertain: bool = False
    request_id: UUID | None = None


def owned_task(db: Session, task_id: UUID, user: User, *, lock: bool = False) -> Task:
    query = select(Task).where(Task.id == task_id, Task.owner_id == user.id)
    if lock:
        query = query.with_for_update()
    task = db.execute(query).scalar_one_or_none()
    if task is None or task.category != CATEGORY:
        raise HTTPException(status_code=404, detail="通用长篇项目不存在")
    return task


def state_for(task: Task) -> dict:
    try:
        return load_state(work_dir(task.id)) or initial_state(str(task.id), str((task.config or {}).get("raw_text") or ""),
                                                             version=(task.config or {}).get("general_longform_policy"))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def delivery_holds(task_id: UUID) -> list[dict]:
    holds = []
    for path in (work_dir(task_id) / "calls").glob("*/*.json"):
        record = read_json(path, {})
        if record.get("status") in {"started", "uncertain"} or record.get("response_status") in {"failed", "incomplete", "cancelled"}:
            holds.append({key: record.get(key) for key in ("request_id", "action", "status", "response_id", "response_status", "error")})
    return holds


@router.get("/prompt-manifest")
def prompt_manifest(version: str | None = None) -> dict:
    try:
        manifest = get_manifest(version)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {key: manifest[key] for key in ("category", "label", "version", "source_sha256", "text_sha256", "paragraph_count", "sections", "stage_sections", "raw_reference_generation_allowed")} | {
        "reference_examples": manifest.get("reference_examples", []), "reference_policy": manifest.get("reference_policy", {}),
        "available_versions": available_versions(), "default_version": get_manifest()["version"],
    }


@router.get("/{task_id}")
def get_project(task_id: UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    task = owned_task(db, task_id, user)
    state = public_state(state_for(task))
    state.update(task_status=task.status, progress=task.progress, current_step=task.current_step, task_error=task.error_message)
    logs = []
    for path in sorted((work_dir(task_id) / "calls").glob("*/*.json")):
        try:
            item = read_json(path, {})
            logs.append({key: item.get(key) for key in ("action", "provider", "model", "status", "started_at", "finished_at", "estimated_cost", "cost_status", "usage", "error", "prompt_version", "prompt_source_sha256")})
        except (OSError, ValueError):
            continue
    state["call_logs"] = sorted(logs, key=lambda item: item.get("started_at") or "")[-200:]
    state["delivery_holds"] = delivery_holds(task_id)
    return state


@router.post("/{task_id}/actions")
def run_action(task_id: UUID, body: ActionRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    task = owned_task(db, task_id, user, lock=True)
    previous_request = (task.config or {}).get("general_longform_request", {})
    if body.request_id and str(body.request_id) == previous_request.get("id"):
        return {"task_id": str(task.id), "status": task.status, "request_id": previous_request["id"]}
    cfg = dict(task.config or {})
    held_resume = body.action == "resume" and any(cfg.get(key) for key in ("generation_hold", "paid_call_delivery_uncertain", "paid_call_manual_resume_required"))
    if (task.status in BUSY and not held_resume) or cfg.get("active_run_id"):
        raise HTTPException(status_code=409, detail="当前任务正在运行或排队，同一本书严格串行")
    state = state_for(task)
    holds = delivery_holds(task_id)
    if body.action != "resume" and holds:
        raise HTTPException(status_code=409, detail="上次模型调用交付待核对，请先恢复同一请求，避免重复付费")
    if body.action != "resume" and any(slot.get("accepted") and not slot["accepted"].get("exported") for slot in state["chapters"].values()):
        raise HTTPException(status_code=409, detail="上一章的配套 Word 尚未完整交付，请先恢复上次操作补齐两份文件")
    if body.outline_version is not None and body.outline_version != state["outline_version"]:
        raise HTTPException(status_code=409, detail="大纲版本已更新，请刷新后再操作")
    if body.action == "resume":
        request = dict(state.get("last_request") or {})
        if not request:
            raise HTTPException(status_code=400, detail="没有可恢复的请求")
        if request.get("id") in state["completed_requests"]:
            raise HTTPException(status_code=409, detail="上一阶段已完成，请选择下一章或其他明确操作")
        if not body.retry_uncertain and any(not item.get("response_id") or item.get("response_status") in {"failed", "incomplete", "cancelled"} for item in holds):
            raise HTTPException(status_code=409, detail="请先核对待定调用并勾选明确重试；本次没有新增请求或费用")
        request["retry_uncertain"] = body.retry_uncertain
    else:
        request = {**body.model_dump(exclude_none=True, exclude={"request_id"}), "id": str(body.request_id or uuid4()), "created_at": now_iso()}
    if request["action"] in {"revise_outline", "revise_chapter"} and not str(request.get("feedback") or "").strip():
        raise HTTPException(status_code=400, detail="请填写具体的局部修改要求")
    if request["action"] in {"next_chapter", "revise_chapter", "assemble"} and not state["confirmed"]:
        raise HTTPException(status_code=409, detail="先确认大纲，再生成正文")
    if request["action"] == "revise_chapter" and not request.get("chapter_number"):
        raise HTTPException(status_code=400, detail="请选择需要局部修订的章号")
    if request["action"] == "plan" and (state.get("outline") or state.get("outline_candidate")):
        raise HTTPException(status_code=409, detail="已有大纲或待补齐候选，请局部修订或确认")
    from app.api.routers.tasks import _ensure_generation_capacity
    if task.status not in BUSY:
        _ensure_generation_capacity(db)
    cfg = dict(task.config or {})
    cfg["general_longform_request"] = request
    cfg["general_longform_phase"] = state["phase"]
    cfg["pause_requested"] = False
    if body.retry_uncertain or (body.action == "resume" and all(item.get("response_id") for item in holds)):
        for key in ("paid_call_delivery_uncertain", "paid_call_manual_resume_required", "generation_hold"):
            cfg.pop(key, None)
    task.config = cfg
    task.status, task.current_step, task.error_message = TaskStatus.PENDING, "通用长篇：等待执行指定阶段", None
    db.commit()
    from app.api.routers.tasks import _dispatch_task, _mark_dispatch_failure_pending
    try:
        _dispatch_task(task)
    except Exception as exc:
        _mark_dispatch_failure_pending(task, exc)
        db.commit()
        raise HTTPException(status_code=503, detail="操作已保存，任务队列暂未响应，将从断点恢复") from exc
    return {"task_id": str(task.id), "status": task.status, "request_id": request["id"]}


@router.post("/{task_id}/materials")
async def upload_material(task_id: UUID, file: UploadFile = File(...),
                          role: Literal["auto", "source", "outline", "chapter", "editorial", "report", "instruction"] = Form("auto"),
                          confirmed: bool = Form(False), db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    content = await file.read(10_000_001)
    if not content or len(content) > 10_000_000:
        raise HTTPException(status_code=400, detail="请上传1字节至10 MB的材料")
    suffix = Path(file.filename or "").suffix.lower()
    metadata = None
    try:
        if suffix == ".docx":
            _, metadata = inspect_docx(content)
            text = extract_docx_text(content)
        elif suffix in {".txt", ".md"}:
            text = content.decode("utf-8-sig")
        else:
            raise ValueError("支持 docx、UTF-8 txt 或 md")
        if not text.strip() or len(text) > 250000:
            raise ValueError("材料正文为空或超过250000字符；没有截断材料")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"材料读取失败：{exc}") from exc
    task = owned_task(db, task_id, user, lock=True)
    if task.status in BUSY or (task.config or {}).get("active_run_id"):
        raise HTTPException(status_code=409, detail="当前阶段运行中，请结束后再补充材料")
    directory = work_dir(task_id)
    state = state_for(task)
    digest = sha256(content).hexdigest()
    if any(item["sha256"] == digest for item in state["materials"]):
        existing = next(item for item in state["materials"] if item["sha256"] == digest)
        if not existing.get("imported") and (role != existing.get("role") or confirmed != existing.get("confirmed")):
            existing.update(role=role, confirmed=confirmed)
            existing.pop("identified", None)
            atomic_json(directory / "state.json", state)
            db.commit()
        return {"status": "duplicate", "id": existing["id"], "sha256": digest}
    relative = f"materials/{digest}/source{suffix}"
    path = directory / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    text_path = f"materials/{digest}/text.txt"
    atomic_text(directory / text_path, text)
    item = {"id": digest[:24], "filename": Path(file.filename or "材料").name, "sha256": digest,
            "role": role, "confirmed": confirmed, "text_path": text_path, "file_path": relative,
            "metadata": metadata, "characters": len(text), "created_at": now_iso(), "imported": False,
            "provider": "user_upload", "query": Path(file.filename or "材料").name, "status": "uploaded", "error": None, "generated_card_count": 0}
    state["materials"].append(item)
    atomic_json(directory / "state.json", state)
    db.commit()
    return {"status": "uploaded", "id": item["id"], "characters": len(text)}


@router.post("/{task_id}/pause")
def pause_project(task_id: UUID, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    task = owned_task(db, task_id, user, lock=True)
    cfg = dict(task.config or {})
    if task.status not in BUSY:
        return {"task_id": str(task.id), "status": task.status}
    cfg.update(pause_requested=True, pause_requested_at=now_iso())
    if not cfg.get("active_run_id"):
        state = state_for(task)
        state["phase"] = "paused"
        state["last_request"] = cfg.get("general_longform_request") or {"id": str(uuid4()), "action": "plan", "created_at": now_iso()}
        atomic_json(work_dir(task_id) / "state.json", state)
        task.status = TaskStatus.PAUSED
        cfg["pause_requested"] = False
    task.config = cfg
    task.current_step = "通用长篇：已请求暂停，正在结束当前调用并保留断点"
    db.commit()
    return {"task_id": str(task.id), "status": task.status}


@router.get("/{task_id}/chapters/{number}/content")
def chapter_content(task_id: UUID, number: int, kind: Literal["accepted", "draft"] = "accepted",
                    db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    task = owned_task(db, task_id, user)
    slot = state_for(task)["chapters"].get(str(number), {})
    record = slot.get("accepted" if kind == "accepted" else "candidate") or {}
    relative = record.get("acceptedContent") if kind == "accepted" else record.get("repairedContent") or record.get("draftContent")
    if not relative:
        raise HTTPException(status_code=404, detail="该版本正文尚未生成")
    directory = work_dir(task_id).resolve()
    path = (directory / relative).resolve()
    if not path.is_relative_to(directory) or not path.is_file():
        raise HTTPException(status_code=404, detail="正文文件不存在")
    content = path.read_text(encoding="utf-8")
    if kind == "accepted" and sha256(content.encode()).hexdigest() != record.get("content_sha256"):
        raise HTTPException(status_code=409, detail="验收正文完整性校验失败")
    return {"content": content, "kind": kind, "version": record["version"]}


@router.get("/{task_id}/artifacts/{artifact_id}")
def download_artifact(task_id: UUID, artifact_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> FileResponse:
    task = owned_task(db, task_id, user)
    artifact = next((item for item in state_for(task)["artifacts"] if item["id"] == artifact_id), None)
    if artifact is None:
        raise HTTPException(status_code=404, detail="导出文件不存在")
    directory = (work_dir(task_id) / "exports").resolve()
    path = (directory / artifact["filename"]).resolve()
    if not path.is_relative_to(directory) or not path.is_file():
        raise HTTPException(status_code=404, detail="导出文件不存在")
    if sha256(path.read_bytes()).hexdigest() != artifact["sha256"]:
        raise HTTPException(status_code=409, detail="导出文件完整性校验失败")
    return FileResponse(path, filename=artifact["filename"], media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
