"""自动连跑：下一步该由系统自己点哪一个按钮，以及在哪里必须停下来问人。

纯状态读者：不发模型调用、不改 prompt、不改任何验收门。工作区的「自动连跑」
开关用的就是这张表，所以自动跑的每一步都和人工点按钮走的是同一条接口、同一套
校验；它不会、也不能做出人工做不到的事。每一次「停」都带一句人能看懂的原因。
"""
from __future__ import annotations

from typing import Any

# 自动连跑可以直接执行的步骤（全部复用已有动作，不新增工作流）。
AUTO_ACTIONS = frozenset({
    "confirm_outline", "next_chapter", "assemble", "resume",
    "repair_outline_candidate", "revise_outline_local", "repair_outline_revision",
    "revise_chapter",
})
# 会重新做一次全网检索、等于重新决定选题方向的步骤：自动连跑默认不碰。
SEARCH_ACTIONS = frozenset({"plan", "revise_outline"})
RUNNING_STATUSES = frozenset({
    "pending", "processing", "fetching_topics", "filtering_topics",
    "generating_outline", "writing_chapters", "fact_checking",
})
HOLD_KEYS = ("generation_hold", "paid_call_delivery_uncertain", "paid_call_manual_resume_required")

GAPS_FEEDBACK = ("请只做一件事：把 research_gaps 里无法核验的内容从章节计划中删除，或改成现有已核验来源"
                 "能够支撑的表述，并清空 research_gaps；其余章节结构、书名方案与文字保持原样。")


def _text(value: Any, limit: int = 400) -> str:
    return str(value if value is not None else "").strip()[:limit]


def _step(action: str, label: str, reason: str, *, params: dict | None = None) -> dict[str, Any]:
    return {"action": action, "label": label, "reason": reason, "params": dict(params or {}),
            "requires_feedback": bool((params or {}).get("feedback")), "stop": None}


def _stop(reason: str) -> dict[str, Any]:
    return {"action": None, "label": "", "reason": "", "params": {}, "requires_feedback": False, "stop": reason}


def _trusts_source_policy(state: dict[str, Any]) -> bool:
    """True when the author chose to write from the live search without a second pass."""
    return str(state.get("source_policy") or "verified") == "search_trusted"


def _holds(state: dict[str, Any], config: dict[str, Any]) -> bool:
    holds = state.get("delivery_holds")
    if isinstance(holds, list) and any(isinstance(item, dict) for item in holds):
        return True
    return any(config.get(key) for key in HOLD_KEYS)


def _records(state: dict[str, Any]) -> dict[int, dict[str, Any]]:
    chapters = state.get("chapters")
    if not isinstance(chapters, dict):
        return {}
    out: dict[int, dict[str, Any]] = {}
    for key, slot in chapters.items():
        try:
            number = int(key)
        except (TypeError, ValueError):
            continue
        if number >= 1 and isinstance(slot, dict):
            out[number] = slot
    return out


def _blocking(chapters: dict[int, dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    blocked: dict[int, list[dict[str, Any]]] = {}
    for number, slot in chapters.items():
        candidate = slot.get("candidate")
        if not isinstance(candidate, dict):
            continue
        issues = [item for item in candidate.get("issues") or [] if isinstance(item, dict)
                  and str(item.get("severity") or "").lower() in {"high", "severe", "blocking"}]
        if issues:
            blocked[number] = issues
    return blocked


def _issue_feedback(issues: list[dict[str, Any]], headline: str) -> str:
    lines = [f"- {_text(item.get('description') or item.get('message') or item.get('title'), 200)}" for item in issues[:8]]
    return headline + "\n" + "\n".join(lines)


def _accepted_words(chapters: dict[int, dict[str, Any]]) -> int:
    return sum(int((slot.get("accepted") or {}).get("word_count") or 0) for slot in chapters.values())


def auto_next(state: dict[str, Any] | None, *, task_status: str = "", config: dict[str, Any] | None = None,
              allow_search: bool = False) -> dict[str, Any]:
    """返回自动连跑的下一步，或它必须停下来的原因。

    只有两种情况会停：需要一次新的全网检索（等于重新决定选题方向），或者出现了
    必须由人处理的闸门/缺口。停下时一定带一句原因，工作区直接显示这句话。
    """
    state = state if isinstance(state, dict) else {}
    config = config if isinstance(config, dict) else {}
    status = _text(task_status or state.get("task_status"), 40)
    phase = _text(state.get("phase") or config.get("general_longform_phase"), 60)

    if _holds(state, config):
        return _stop("上次模型调用的交付状态待核对（或存在人工闸门），需要你确认后才会继续。")
    if status in RUNNING_STATUSES:
        return _stop("当前阶段正在跑，自动连跑会等它结束。")
    if phase == "completed":
        return _stop("全书已验收并合稿完成，自动连跑结束。")
    if phase == "paused":
        return _step("resume", "从断点继续当前阶段", "已在持久化断点暂停，接着上次的位置跑。")
    if phase == "awaiting_materials":
        return _stop("这份项目在等你上传材料（已有大纲/正文），自动连跑不会替你猜。")

    plan = state.get("outline") if isinstance(state.get("outline"), dict) else None
    candidate = state.get("outline_candidate") if isinstance(state.get("outline_candidate"), dict) else None
    reusable = (isinstance(state.get("last_research"), dict) and bool(state["last_research"].get("web_call_completed"))
                and isinstance(state.get("sources"), dict) and bool(state.get("sources")))

    if not state.get("confirmed"):
        gaps = [item for item in (plan or {}).get("research_gaps") or [] if _text(item)]
        # The author may have chosen to write from the live search as-is; then the
        # outstanding points are a record, not a blocker, and the run confirms.
        if plan and gaps and not _trusts_source_policy(state):
            if reusable:
                return _step("revise_outline_local", "用已付费证据收敛待核实项（不重新联网）",
                             "research_gaps 非空时确认会被拒绝；这一步只用已核验来源改写大纲，不再联网。",
                             params={"feedback": GAPS_FEEDBACK})
            if allow_search:
                return _step("revise_outline", "重新联网补齐待核实材料",
                             "断点里没有可复用的检索证据，只能重新检索一次。",
                             params={"feedback": GAPS_FEEDBACK})
            return _stop("大纲还有待核实材料，需要一次联网检索才能补齐；自动连跑没有开启重新检索。")
        if plan:
            index = plan.get("recommended_index")
            title = ""
            packaging = plan.get("packaging")
            if isinstance(packaging, list) and isinstance(index, int) and 0 <= index < len(packaging):
                title = _text((packaging[index] or {}).get("title"), 60)
            trusted_note = ("；已选择直接采用联网检索结果，" + str(len(gaps)) + " 条未取得原文的要点只作留痕"
                            if gaps and _trusts_source_policy(state) else "")
            return _step("confirm_outline", "自动确认推荐方案" + (f"（《{title}》）" if title else ""),
                         "大纲已通过结构校验；自动连跑按推荐方案确认，然后开始逐章写作" + trusted_note + "。",
                         params={"packaging_index": index if isinstance(index, int) else 0})
        if candidate and reusable:
            return _step("repair_outline_candidate", "按校验结果修复候选大纲（不重新检索）",
                         "候选大纲已保留，复用本书已核验的检索证据做局部修正。")
        if allow_search:
            return _step("plan" if not candidate else "revise_outline",
                         "研究并生成待确认大纲" if not candidate else "重新联网补齐候选大纲",
                         "断点里没有可复用的检索证据，只能重新检索一次。",
                         params={} if not candidate else {"feedback": GAPS_FEEDBACK})
        return _stop("还没有可用的检索证据，需要一次联网检索；自动连跑没有开启重新检索。")

    chapters = _records(state)
    unexported = [number for number, slot in sorted(chapters.items())
                  if isinstance(slot.get("accepted"), dict) and not slot["accepted"].get("exported")]
    if unexported:
        return _step("resume", f"补齐第{unexported[0]}章的两份 Word",
                     "上一章验收通过但配套 Word 还没写完，先补齐再往下写。")
    blocked = _blocking(chapters)
    if blocked:
        number = sorted(blocked)[0]
        return _step("revise_chapter", f"按验收问题自动修订第{number}章",
                     "验收门拦下了高/严重问题；自动连跑按报告里的问题做一次局部修订并重新验收。",
                     params={"chapter_number": number,
                             "feedback": _issue_feedback(blocked[number], f"请只修第{number}章以下验收问题：")})
    accepted = sorted(number for number, slot in chapters.items() if isinstance(slot.get("accepted"), dict))
    pending = 1
    while pending in accepted:
        pending += 1
    planned = len(plan.get("chapters") or []) if isinstance(plan, dict) else 0
    if planned and pending <= planned:
        label = f"继续完成第{pending}章" if chapters.get(str(pending)) else f"研究并写作第{pending}章"
        return _step("next_chapter", label, "严格串行：一次只推进一章，验收通过后自动进入下一章。")
    if not planned:
        return _stop("大纲里没有可写的章节；请先确认大纲或做一次局部修订。")

    from app.services.general_longform_guidance import final_audit_actions, length_revision_actions  # 延迟导入，避免循环引用

    length_actions = length_revision_actions(state)
    if length_actions:
        return _step(length_actions[0]["action"], "自动把字数调回合稿区间",
                     f"全书有效正文 {_accepted_words(chapters)} 字不在 100000-110000 区间；"
                     "自动连跑按面板预填好的章节修订执行一次，再继续合稿。",
                     params=dict(length_actions[0].get("params") or {}))
    audit_actions = final_audit_actions(state)
    if audit_actions:
        return _step(audit_actions[0]["action"], "按终审报告自动修订对应章节",
                     "全书终审有高/严重问题；自动连跑按报告预填的要求做一次局部修订。",
                     params=dict(audit_actions[0].get("params") or {}))
    return _step("assemble", "全书终审与合稿 Word", "全部章节已验收，自动连跑直接进入终审与合稿。")
