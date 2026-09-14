"""Deterministic workflow doubles: no network, no paid APIs, not quality evidence."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from uuid import uuid4

from app.services import general_longform as engine
from app.services.general_longform_runtime import now_iso

SOURCE = {"id": "Sverified", "url": "https://example.org/research", "title": "公开研究资料",
          "authors": "Research Institute", "year": "2026", "bibliography": "Research Institute. Public Study. 2026. https://example.org/research",
          "approved_summary": "经测试固定的抽象材料，不是线上研究结论。", "supported_claims": ["测试的条件与边界"],
          "limitations": ["确定性测试数据"], "verification": "page_read_and_abstracted", "read_scope": "测试页全文",
          "retrieved_at": "2026-09-14T08:00:00+00:00", "file_sha256": "a" * 64, "text_sha256": "b" * 64}


def outline():
    return {"topic": "普通人的判断与选择", "domain": "认知成长与效能提升", "audience": "普通读者", "central_question": "如何建立有边界的判断", "voice": "清楚自然",
            "scope_exclusions": ["收益承诺"], "packaging": [{"direction": f"方向{i}", "title": f"普通人的判断与选择新方式{i}", "description": "读者判断选择需要具体条件。" * 18} for i in range(1, 6)],
            "recommended_index": 0, "recommendation": "测试推荐", "target_words": 105000,
            "chapters": [{"number": number, "title": f"观察与选择{number}", "question": f"章{number}的问题", "new_information": f"新增{number}",
                "previous_link": "承接前文", "next_link": "保留问题", "do_not_repeat": [], "source_ids": ["Sverified"], "target_words": 8750,
                "sections": [{"title": f"问题{number}甲", "task": "界定问题", "argument": "说明条件", "material": "公开材料", "source_ids": ["Sverified"], "target_words": 4375},
                             {"title": f"问题{number}乙", "task": "比较取舍", "argument": "推导边界", "material": "公开材料", "source_ids": ["Sverified"], "target_words": 4375}]} for number in range(1, 13)],
            "handoff": {"terms": {}, "case_allocation": [], "fact_boundaries": ["测试"], "video_basis": "自主选题"}, "research_gaps": []}


def section_text(number, index, words=4375):
    # Distinct synthetic paragraphs make byte-level duplicate guards meaningful.
    return chr(0x5000 + number * 2 + index) * words + "。"


def chapter_text(number, plan=None):
    plan = plan or outline()
    contract = plan["chapters"][number - 1]
    return engine.GeneralLongformRunner._assemble_sections(contract, [section_text(number, i, s["target_words"]) for i, s in enumerate(contract["sections"])])


class FakeGateway:
    def __init__(self, directory: Path):
        self.directory = directory
        self.request_id = str(uuid4())
        self.check_active = lambda: None
        self.calls = []
        self.cache = {}
        self.hooks = {}
        self.plan = outline()
        self.identified = {}

    def call(self, stage, context, **kwargs):
        key = (self.request_id, stage, kwargs.get("slot", ""))
        if key in self.cache:
            return deepcopy(self.cache[key])
        self.calls.append({"stage": stage, "context": deepcopy(context), **kwargs})
        if stage in self.hooks:
            result = self.hooks[stage](context, kwargs)
        elif stage == "reference_analysis":
            result = {"ReferencePatternCards": [{"id": "R1", "function": "表达节奏", "mechanism": "按信息量设置停顿", "avoid": "相同结尾"}], "ApprovedEditingSummary": {"pacing": "随任务推进"}}
        elif stage == "outline":
            result = self.plan
        elif stage == "materials":
            material = context["material"]
            result = self.identified.get(material.get("filename"), {"role": material["role"], "approved_summary": "用户材料中的主题与待核实约束", "approved_notes": "第三段条件需核对", "notes": "测试识别"})
        elif stage == "draft":
            contract = context["chapter"]
            index = contract["sections"].index(context["section"])
            result = section_text(contract["number"], index, context["section"]["target_words"])
        elif stage in {"polish", "refine", "repair"}:
            result = context["candidate"]
        elif stage == "acceptance":
            contract = context["chapter"]
            result = {"summary": f"第{contract['number']}章实质信息", "endingHook": "后续的新问题", "new_information": ["明确的新信息"],
                      "used_source_ids": ["Sverified"], "terms": {}, "cases": [], "completed_sections": [s["title"] for s in contract["sections"]],
                      "planned_vs_actual": [{"section": s["title"], "fulfilled": True, "evidence": "完整对应章节任务"} for s in contract["sections"]], "issues": []}
        elif stage == "final":
            result = {"issues": [], "summary": "确定性结构测试通过，不代表线上作品质量"}
        else:
            raise AssertionError(f"Unexpected fake stage: {stage}")
        response = {"text": result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)}
        self.cache[key] = deepcopy(response)
        return response

    def json(self, stage, context, **kwargs):
        return json.loads(self.call(stage, context, **kwargs)["text"])


def fake_research(gateway, context, **kwargs):
    gateway.calls.append({"stage": "research", "context": deepcopy(context), **kwargs})
    return {"topic": "判断与选择", "domain": "认知成长与效能提升", "sources": {SOURCE["id"]: deepcopy(SOURCE)},
            "gaps": [], "errors": [], "rawTextIncluded": False, "web_call_completed": True, "retrieved_at": now_iso()}


def new_state():
    return engine.initial_state(str(uuid4()))


def run(directory, state, gateway, action, *, request=None, **kwargs):
    request = request or {"id": str(uuid4()), "action": action, "created_at": now_iso(), **kwargs}
    gateway.request_id = request["id"]
    return engine.GeneralLongformRunner(directory, state, request, gateway=gateway).run()


def ready(directory, monkeypatch):
    monkeypatch.setattr(engine, "research", fake_research)
    state, gateway = new_state(), FakeGateway(directory)
    run(directory, state, gateway, "plan")
    assert state["phase"] == "awaiting_outline_confirmation", state.get("last_error")
    run(directory, state, gateway, "confirm_outline")
    assert state["confirmed"], state.get("last_error")
    return state, gateway


def add_material(directory, state, text, *, role="auto", metadata=None, filename="材料.txt", confirmed=False):
    digest = sha256((filename + text).encode()).hexdigest()
    path = f"materials/{digest}/text.txt"
    target = directory / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    item = {"id": digest[:24], "sha256": digest, "text_path": path, "role": role, "filename": filename, "metadata": metadata,
            "confirmed": confirmed, "imported": False}
    state["materials"].append(item)
    return item
