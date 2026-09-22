"""Deterministic next-step guidance for the general-longform workspace.

This module only reads persisted state and task metadata. It makes no model
call, changes no prompt, no acceptance rule and no serial-order rule. Its job is
to turn "the project stopped and I do not know what to click" into one
recommended action plus explicit alternatives, each with a cost note.

Guarantees:

* Every stopped project gets at least one runnable action; a stopped project is
  never left with an empty button list.
* Actions that would fail again in exactly the same way (for example 合稿 while
  the accepted word total is outside 100000-110000) are reported in "avoid" with
  the reason, so the workspace stops offering a button that loops.
* Actions that reuse already-paid work say so, and actions that start new paid
  work say so too, because that is the user's real decision.
"""
from __future__ import annotations

from typing import Any

from app.services.general_longform_autopilot import auto_next

PHASE_LABELS = {
    "needs_outline": "准备策划",
    "awaiting_materials": "等待材料",
    "awaiting_outline_confirmation": "等待确认大纲",
    "ready_for_chapter": "等待下一章操作",
    "writing_chapter": "当前章写作中",
    "ready_for_assembly": "等待全书终审",
    "needs_review": "有待处理项",
    "paused": "断点已保存",
    "completed": "全书已验收",
}

# Mirrors the task statuses the API treats as busy; kept local so this module
# stays importable without touching the router or the ORM.
RUNNING_STATUSES = frozenset({
    "pending", "processing", "fetching_topics", "filtering_topics",
    "generating_outline", "writing_chapters", "fact_checking",
})
HOLD_KEYS = ("generation_hold", "paid_call_delivery_uncertain", "paid_call_manual_resume_required")
TARGET_MIN, TARGET_MAX = 100000, 110000
DEFAULT_STALE_SECONDS = 900

REUSE_NOTE = "复用断点里已完成/已付费的结果，不重复扣费"
NEW_SEARCH_NOTE = "会产生一次新的联网检索与后续生成调用"
NEW_CALL_NOTE = "新增一次生成调用并计入费用"


def _text(value: Any, limit: int = 400) -> str:
    return str(value if value is not None else "").strip()[:limit]


def _action(action: str, label: str, reason: str, *, cost: str = "", params: dict | None = None,
            enabled: bool = True, disabled_reason: str = "", requires_feedback: bool = False,
            tab: str = "") -> dict[str, Any]:
    return {"action": action, "label": label, "reason": reason, "cost": cost,
            "params": dict(params or {}), "enabled": bool(enabled),
            "disabled_reason": _text(disabled_reason, 300), "requires_feedback": bool(requires_feedback),
            "tab": tab}


def _nav(label: str, reason: str, tab: str, *, cost: str = "") -> dict[str, Any]:
    return _action("manual", label, reason, cost=cost, tab=tab)


def _outline(state: dict[str, Any]) -> dict[str, Any] | None:
    plan = state.get("outline")
    return plan if isinstance(plan, dict) else None


def _records(state: dict[str, Any]) -> dict[int, dict[str, Any]]:
    chapters = state.get("chapters")
    if not isinstance(chapters, dict):
        return {}
    records: dict[int, dict[str, Any]] = {}
    for key, slot in chapters.items():
        try:
            number = int(key)
        except (TypeError, ValueError):
            continue
        if number >= 1 and isinstance(slot, dict):
            records[number] = slot
    return records


def accepted_numbers(state: dict[str, Any]) -> list[int]:
    return sorted(number for number, slot in _records(state).items() if isinstance(slot.get("accepted"), dict))


def planned_chapter_total(state: dict[str, Any]) -> int:
    plan = _outline(state)
    chapters = plan.get("chapters") if plan else None
    return len(chapters) if isinstance(chapters, list) else 0


def pending_number(state: dict[str, Any]) -> int | None:
    """The next chapter the serial pipeline must produce, or None when done."""
    total = planned_chapter_total(state)
    accepted = set(accepted_numbers(state))
    for number in range(1, total + 1):
        if number not in accepted:
            return number
    if total:
        return None
    if isinstance(_outline(state), dict):
        return None
    records = _records(state)
    for number in sorted(records):
        if number not in accepted:
            return number
    return None


def accepted_words(state: dict[str, Any]) -> int:
    total = state.get("total_words")
    if isinstance(total, int) and total > 0:
        return total
    words = 0
    for slot in _records(state).values():
        record = slot.get("accepted")
        if isinstance(record, dict) and isinstance(record.get("word_count"), int):
            words += record["word_count"]
    return words


def blocking_issues(issues: Any) -> list[dict[str, Any]]:
    """Same rule as the acceptance gate: anything not low/medium blocks."""
    if not isinstance(issues, list):
        return []
    return [item for item in issues
            if not isinstance(item, dict) or not isinstance(item.get("severity"), str)
            or item["severity"] not in {"low", "medium"}]


def _issue_feedback(issues: list[dict[str, Any]], headline: str) -> str:
    lines = ["- " + _text(item.get("description") if isinstance(item, dict) else item, 200) for item in issues[:5]]
    body = "\n".join(line for line in lines if line.strip(" -"))
    return (headline + "\n" + body +
            "\n请在保持章标题、小节标题与已验收内容边界不变的前提下逐条局部修复，不要重写整章、不要改动其他章。")


def _contract(state: dict[str, Any], number: int) -> dict[str, Any] | None:
    plan = _outline(state)
    chapters = plan.get("chapters") if plan else None
    if isinstance(chapters, list) and 1 <= number <= len(chapters) and isinstance(chapters[number - 1], dict):
        return chapters[number - 1]
    return None


def _chapter_target(state: dict[str, Any], number: int) -> int:
    contract = _contract(state, number)
    words = contract.get("target_words") if contract else None
    return words if isinstance(words, int) and words > 0 else 0


def _accepted_words_of(state: dict[str, Any], number: int) -> int:
    record = _records(state).get(number, {}).get("accepted")
    words = record.get("word_count") if isinstance(record, dict) else None
    return words if isinstance(words, int) and words > 0 else 0


def length_revision_actions(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Turn an out-of-range total word count into concrete per-chapter patches."""
    total = accepted_words(state)
    accepted = accepted_numbers(state)
    if not accepted or TARGET_MIN <= total <= TARGET_MAX:
        return []
    delta = TARGET_MIN - total if total < TARGET_MIN else TARGET_MAX - total
    grow = delta > 0
    ranked = []
    for number in accepted:
        target, words = _chapter_target(state, number), _accepted_words_of(state, number)
        gap = (target - words) if grow else (words - target)
        ranked.append((gap, number))
    if not ranked:
        return []
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    picked = [number for gap, number in ranked if gap > 0][:2]
    if not picked:
        picked = [ranked[0][1]]
    share = max(120, abs(delta) // len(picked))
    actions = []
    for index, number in enumerate(picked):
        amount = share if index < len(picked) - 1 else max(120, abs(delta) - share * (len(picked) - 1))
        if grow:
            feedback = ("全书实际正文 " + str(total) + " 字，需要落回 " + str(TARGET_MIN) + "-" + str(TARGET_MAX) +
                        " 字区间；请在保持本章标题、小节标题和已验收内容边界不变的前提下，只在本章内扩充约 " +
                        str(amount) + " 字：补充新的条件、边界、反例或操作步骤，不要重复已有段落，不要新增小节标题，不要改动其他章。")
            label = "修订第" + str(number) + "章并扩充约" + str(amount) + "字"
        else:
            feedback = ("全书实际正文 " + str(total) + " 字，需要落回 " + str(TARGET_MIN) + "-" + str(TARGET_MAX) +
                        " 字区间；请在保持本章标题、小节标题和全部关键证据不变的前提下，只在本章内压缩约 " +
                        str(amount) + " 字：删除重复表述与铺垫，不要合并或删除小节，不要改动其他章。")
            label = "修订第" + str(number) + "章并压缩约" + str(amount) + "字"
        actions.append(_action("revise_chapter", label,
                               "合稿要求 100000-110000 字；当前 " + str(total) + " 字，差 " + str(abs(delta)) +
                               " 字。系统已按章节缺口算出建议字数，修改要求已填好，可直接提交。",
                               cost=NEW_CALL_NOTE, params={"chapter_number": number, "feedback": feedback},
                               tab="章节"))
    return actions


def chapter_repair_actions(state: dict[str, Any], numbers: list[int] | None = None) -> list[dict[str, Any]]:
    """One-click 局部修订 for chapters whose acceptance gate is still blocking."""
    actions = []
    records = _records(state)
    for number in (numbers if numbers is not None else sorted(records)):
        slot = records.get(number, {})
        candidate = slot.get("candidate") if isinstance(slot.get("candidate"), dict) else None
        if not candidate:
            continue
        issues = blocking_issues(candidate.get("issues"))
        if not issues:
            continue
        actions.append(_action("revise_chapter", "按验收问题修订第" + str(number) + "章",
                               "第" + str(number) + "章有 " + str(len(issues)) + " 条高/严重问题未消除；"
                               "修改要求已按验收报告填好，修订后仍会重新抽取状态并重新过验收门。",
                               cost=NEW_CALL_NOTE, params={"chapter_number": number,
                                                           "feedback": _issue_feedback(issues, "第" + str(number) + "章验收问题：")},
                               tab="章节"))
    return actions


def final_audit_actions(state: dict[str, Any]) -> list[dict[str, Any]]:
    audit = state.get("final_audit")
    issues = blocking_issues(audit.get("issues")) if isinstance(audit, dict) else []
    if not issues:
        return []
    total = max(1, planned_chapter_total(state))
    numbered = [item.get("chapter_number") for item in issues
                if isinstance(item, dict) and isinstance(item.get("chapter_number"), int)]
    numbers = sorted({number for number in numbered if 1 <= number <= total})
    if not numbers:
        numbers = accepted_numbers(state)[-1:]
    actions = []
    for number in numbers[:3]:
        actions.append(_action("revise_chapter", "按终审报告修订第" + str(number) + "章",
                               "全书终审存在高/严重问题；修改要求已按终审报告填好，修订后本书需要重新合稿。",
                               cost=NEW_CALL_NOTE,
                               params={"chapter_number": number,
                                       "feedback": _issue_feedback(issues, "全书终审问题：")},
                               tab="章节"))
    return actions


def reusable_evidence(state: dict[str, Any]) -> bool:
    research_state = state.get("last_research")
    sources = state.get("sources")
    return (isinstance(research_state, dict) and bool(research_state.get("web_call_completed"))
            and isinstance(sources, dict) and bool(sources))


def outline_revision_retry(state: dict[str, Any]) -> dict[str, Any] | None:
    """Offer the cheap retry when a local revision failed on patch shape only."""
    context = state.get("outline_revision_context")
    if not isinstance(context, dict) or not isinstance(context.get("evidence"), dict) or not context["evidence"]:
        return None
    if context.get("outline_version") != state.get("outline_version"):
        return None
    if not _text(context.get("feedback")):
        return None
    return _action("repair_outline_revision", "再次尝试大纲修订（复用上次检索，不重新联网）",
                   "上次修订只失败在模型返回的补丁形状上；这次复用同一批已核验来源与同一份修改要求，只重跑一次大纲局部修订。",
                   cost=NEW_CALL_NOTE, tab="规划")


def _holds(state: dict[str, Any]) -> list[dict[str, Any]]:
    holds = state.get("delivery_holds")
    return [item for item in holds if isinstance(item, dict)] if isinstance(holds, list) else []


def _heartbeat_age(config: dict[str, Any]) -> float | None:
    from datetime import datetime, timezone
    for key in ("active_run_heartbeat_at", "active_run_started_at"):
        raw = config.get(key)
        if not raw:
            continue
        parsed = None
        try:
            parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - parsed).total_seconds()
    return None


def build_guidance(state: dict[str, Any] | None, *, task_status: str = "", config: dict[str, Any] | None = None,
                   stale_seconds: int | None = None) -> dict[str, Any]:
    """Return the single next step, plus what 自动连跑 may take on its own."""
    result = _guidance(state, task_status=task_status, config=config, stale_seconds=stale_seconds)
    safe_state = state if isinstance(state, dict) else {}
    safe_config = config if isinstance(config, dict) else {}
    result["auto"] = {
        "enabled": bool(safe_state.get("auto_run")),
        **auto_next(safe_state, task_status=task_status, config=safe_config,
                    allow_search=bool(safe_config.get("general_longform_auto_run_allow_search"))),
    }
    return result


def _guidance(state: dict[str, Any] | None, *, task_status: str = "", config: dict[str, Any] | None = None,
              stale_seconds: int | None = None) -> dict[str, Any]:
    """Return the single next step (plus alternatives) for any 通用长篇 state."""
    state = state if isinstance(state, dict) else {}
    config = config if isinstance(config, dict) else {}
    status = _text(task_status or state.get("task_status"), 40)
    phase = _text(state.get("phase") or config.get("general_longform_phase"), 60)
    error = _text(state.get("last_error") or config.get("general_longform_error"), 600)
    threshold = int(stale_seconds or DEFAULT_STALE_SECONDS)
    holds = _holds(state)
    flags = [key for key in HOLD_KEYS if config.get(key)]
    can_resume = bool(state.get("can_resume"))
    notes: list[str] = []
    avoid: list[dict[str, Any]] = []
    alternatives: list[dict[str, Any]] = []
    primary: dict[str, Any] | None = None
    review = phase == "needs_review" or status in {"manual_review", "needs_review"}
    kind, headline, detail = "unknown", "", ""

    def resume_action(label: str, reason: str, *, retry: bool = False, cost: str = REUSE_NOTE) -> dict[str, Any]:
        return _action("resume", label, reason, cost=cost, params={"retry_uncertain": retry})

    if holds or flags:
        needs_confirmation = any(not item.get("response_id") or item.get("response_status") in {"failed", "incomplete", "cancelled"}
                                 for item in holds)
        plane = "hold"
        headline = "上次模型调用交付状态待核对，自动流程已停下"
        detail = ("断点里保留了这次付费调用。已有响应 ID 时先取回原响应；没有响应 ID 时必须先核对调用记录，"
                  "再勾选「我已核对交付待定调用」后点「恢复上次操作」。")
        primary = resume_action("恢复上次操作（先核对交付待定调用）",
                                "复用同一个请求 id：优先取回原响应，没有响应 ID 时只有勾选确认后才会发出新请求。",
                                retry=needs_confirmation)
        if flags:
            notes.append("任务配置里存在人工闸门：" + "、".join(flags) + "；自动看门狗会跳过它，必须人工处理。")
        notes.append("刷新或等待都不会自动放行，需要你在工作区确认一次。")
        alternatives.append(_nav("查看调用记录与错误", "在「日志」页核对动作、模型、状态和错误后再决定是否重试。", "日志"))
        return _result(plane, headline, detail, primary, alternatives, avoid, notes)

    if status in RUNNING_STATUSES:
        age = _heartbeat_age(config)
        kind = "running"
        if age is not None and age > threshold:
            headline = "处理中心跳已过期，等待系统自动重排"
            detail = ("心跳已 " + str(int(age // 60)) + " 分钟未更新（阈值 " + str(threshold // 60) +
                      " 分钟）。看门狗会按 STALE_PROCESSING_REQUEUE_SECONDS 自动重排，不需要手动点按钮。")
        elif age is not None:
            headline = "正在执行，等它跑完"
            detail = "当前阶段正在跑，心跳 " + str(int(age)) + " 秒前更新。"
        else:
            headline = "正在执行，等它跑完"
            detail = "当前阶段正在跑，尚未记录运行心跳。"
        notes.append("不要重复点同一个按钮：同一本书严格串行，重复提交会返回 409。")
        notes.append("处理中心跳超过 " + str(threshold // 60) + " 分钟未更新会自动重排；一直不动先看 worker/beat 是否在消费队列。")
        alternatives.append(_nav("查看当前阶段与调用记录", "在「日志」页确认最近一次调用的动作、模型与状态。", "日志"))
        return _result(kind, headline, detail, None, alternatives, avoid, notes)

    if status == "completed" or phase == "completed":
        kind = "done"
        headline = "全书已验收并合稿完成"
        detail = "不需要再推进；下载、改稿都在工作区里。"
        notes.append("已验收正文以哈希校验为准，不允许直接改正文；改稿走「指定章局部修订」，修订后会重新过验收门。")
        alternatives.append(_nav("下载大纲 / 章节 / 全书 Word", "在「文件」页按版本下载正文与来源/编辑记录两份文件。", "文件"))
        alternatives.append(_nav("查看全书终审报告", "在「章节」页查看终审问题清单与正反方法复核。", "章节"))
        return _result(kind, headline, detail, None, alternatives, avoid, notes)

    if status == "failed":
        kind = "failed"
        headline = "任务失败，等待自动重试"
        detail = "失败任务会按 FAILED_TASK_AUTO_RECOVERY_*（默认 600 秒后、最多 24 次）自动重试；反复失败看错误信息。"
        if can_resume:
            primary = resume_action("立即恢复上次操作", "跳过等待，直接用同一个请求断点继续。")
        notes.append("若错误是额度、密钥或来源问题，先在「日志」页确认原因再恢复，避免重复失败。")
        alternatives.append(_nav("查看调用记录与错误", "在「日志」页看到具体 provider、状态和错误。", "日志"))
        return _result(kind, headline, detail, primary, alternatives, avoid, notes)

    if phase == "paused" or config.get("pause_requested"):
        kind = "paused"
        headline = "已在持久化断点暂停"
        detail = "点「恢复上次操作」从同一章继续；已写的部分稿和已付费的调用都还在断点里。"
        primary = resume_action("恢复上次操作", "从断点继续当前阶段，不重复已完成、已付费的节点。")
        alternatives.append(_nav("先看已有文件", "暂停时已落盘的候选稿可以在「文件」页下载。", "文件"))
        return _result(kind, headline, detail, primary, alternatives, avoid, notes)

    if phase == "awaiting_materials":
        pending_materials = [item for item in state.get("materials") or []
                             if isinstance(item, dict) and not item.get("imported")]
        kind = "awaiting_user"
        headline = "等待上传材料"
        detail = "在「材料」页上传已有大纲、正文或配套资料后点「识别材料并继续当前阶段」。"
        primary = _action("import_materials", "识别材料并继续当前阶段",
                          "按内容判断角色与真实进度；已有确认大纲和连续前文时只续写一章。",
                          cost=NEW_CALL_NOTE, enabled=bool(pending_materials),
                          disabled_reason="" if pending_materials else "还没有待识别的材料文件；先在「材料」页上传 Word/TXT/MD",
                          tab="材料")
        alternatives.append(_nav("上传材料", "支持 docx、UTF-8 txt、md，每份最多 10 MB / 25 万字符。", "材料"))
        return _result(kind, headline, detail, primary, alternatives, avoid, notes)

    plan = _outline(state)
    candidate = state.get("outline_candidate") if isinstance(state.get("outline_candidate"), dict) else None

    if not plan and candidate:
        kind = "needs_review" if review else "awaiting_user"
        reusable = reusable_evidence(state)
        headline = "大纲候选已保留，等你自己决定怎么改"
        detail = "候选没有确认、也没有进入正文；已核验的检索证据仍在断点里。"
        primary = _action("repair_outline_candidate", "按校验结果修复候选大纲（不重新检索）",
                          "复用本书已完成并核验过的检索证据，只补不合格字段，其余章节保持原样；通过后仍需你确认。",
                          cost=NEW_CALL_NOTE, enabled=reusable,
                          disabled_reason="" if reusable else "断点里没有可复用的检索证据；请改用大纲局部修订或重新研究")
        retry = outline_revision_retry(state)
        if retry:
            # A failed 局部修订 is cheaper to retry than to re-research.
            alternatives.append(primary)
            primary = retry
        if "大纲补丁" in error:
            notes.append("上次失败是模型返回的大纲补丁形状问题：新版本会先丢弃不允许的全局字段并自动局部重试一次，"
                         "仍然不行才会停下来让你选，不会把你直接踢回重新检索。")
        alternatives.append(_action("revise_outline", "按我的要求修订大纲（重新核查来源）",
                                    "要改内容或换方向时用它；这条路径会按既定流程重新核查来源。",
                                    cost=NEW_SEARCH_NOTE, requires_feedback=True, tab="规划"))
        alternatives.append(_nav("上传已有确认大纲", "在「材料」页上传 Word，识别后从真实进度接续。", "材料"))
        return _result(kind, headline, detail, primary, alternatives, avoid, notes)

    if not plan:
        kind = "needs_review" if review else "awaiting_user"
        if review:
            headline = "上次操作停在检索或大纲阶段，断点已保留"
            detail = error or "已完成的参考抽象、联网检索和实际读取的页面全文都在断点里。"
        else:
            headline = "还没有大纲，先做一次策划"
            detail = "研究 -> 五组包装与 12-15 章大纲 -> 你确认后才开始正文。"
        entry = _action("plan", "研究并生成待确认大纲（新的联网检索）",
                        "从选题开始完整跑一次研究、包装与大纲；会发起新的联网检索。",
                        cost=NEW_SEARCH_NOTE)
        if can_resume:
            primary = resume_action("恢复上次操作（用已付费的检索继续）" if review else "继续上次策划",
                                    "同一个请求 id、同一批已读取页面，只重跑未完成的来源抽象。")
            alternatives.append(entry)
        else:
            primary = entry
        alternatives.append(_nav("上传逐字稿 / 选题材料", "没给视频链接或想指定方向时，在「材料」页上传后识别。", "材料"))
        if "哈希" in error or "固定提示词版本" in error:
            notes.append("这是项目固定的 prompt 源文件校验失败，属环境问题而非内容问题："
                         "用 python scripts/import_general_longform_prompt.py 恢复对应版本后再打开工作区。")
        notes.append("没有可恢复断点时才会走「研究并生成待确认大纲」；这一步是唯一会重新联网检索的入口。")
        return _result(kind, headline, detail, primary, alternatives, avoid, notes)

    if not state.get("confirmed"):
        gaps = plan.get("research_gaps")
        gaps = gaps if isinstance(gaps, list) else []
        trusted = str(state.get("source_policy") or "verified") == "search_trusted"
        kind = "needs_review" if review else "awaiting_user"
        if gaps and trusted:
            headline = "已选择直接采用联网检索结果：大纲可以确认了"
            detail = ("待核实要点不再阻断确认（仍有 " + str(len(gaps)) + " 条未取得原文的要点留在记录里，仅作留痕）。"
                      "确认只生成确认版大纲 Word，不会同时开写第一章。")
            primary = _action("confirm_outline", "确认选中包装与大纲",
                              "确认后进入逐章写作；包装（书名与简介）在开始正文后固定。",
                              cost="不产生生成调用", tab="规划")
            alternatives.append(_action("set_source_policy", "改回二次核实门",
                                        "想恢复严格核实门时用它；改回后要先补齐或改掉这些待核实项才能确认。",
                                        cost="不产生生成调用", params={"policy": "verified"}, tab="规划"))
            alternatives.append(_nav("先看五组包装", "在「规划」页对比书名、简介与推荐理由再确认。", "规划"))
        elif gaps:
            headline = "大纲还有待核实材料：可以补齐，也可以直接采用检索结果"
            detail = ("research_gaps 非空时确认会被拒绝。要么补齐或改掉这些待核实项，"
                      "要么把来源策略改成「直接采用联网检索结果」，不再做二次核实。")
            primary = _action("revise_outline", "按待核实项修订大纲",
                              "把 research_gaps 里的内容补齐或改掉，通过后仍需你确认。",
                              cost=NEW_SEARCH_NOTE, requires_feedback=True,
                              params={"feedback": "请按 research_gaps 补齐或替换待核实材料，保持其余章节不变：" +
                                                  "；".join(_text(item, 120) for item in gaps[:6])},
                              tab="规划")
            alternatives.append(_action("set_source_policy", "直接采用联网检索结果（不再二次核实）",
                                        "不想再为核实花钱或来源读不到时用它：不产生任何调用，改完直接确认大纲开写；"
                                        "未取得原文的要点会留在记录里。",
                                        cost="不产生生成调用", params={"policy": "search_trusted"}, tab="规划"))
        else:
            headline = "五组包装和大纲已就绪，等你确认"
            detail = "选一个书名/简介方案后点确认。确认只生成确认版大纲 Word，不会同时开写第一章。"
            primary = _action("confirm_outline", "确认选中包装与大纲",
                              "确认后进入逐章写作；包装（书名与简介）在开始正文后固定。",
                              cost="不产生生成调用", tab="规划")
        retry = outline_revision_retry(state)
        if retry:
            alternatives.append(primary)
            primary = retry
            headline = "上次大纲修订没成功，可以先廉价重试"
            detail = "断点里留着这次修订的检索证据和你的修改要求；先复用它们重跑一次局部修订，不必重新联网。"
        alternatives.append(_action("revise_outline", "先局部修改大纲",
                                    "书名、简介或章节任务要改时用它；会重新核查来源。",
                                    cost=NEW_SEARCH_NOTE, requires_feedback=True, tab="规划"))
        alternatives.append(_nav("先看五组包装", "在「规划」页对比书名、简介与推荐理由再确认。", "规划"))
        return _result(kind, headline, detail, primary, alternatives, avoid, notes)

    # Outline confirmed: serial chapter work, or final assembly.
    pending = pending_number(state)
    records = _records(state)
    unexported = [number for number, slot in sorted(records.items())
                  if isinstance(slot.get("accepted"), dict) and not slot["accepted"].get("exported")]
    length_actions = length_revision_actions(state)
    audit_actions = final_audit_actions(state)

    if unexported:
        kind = "needs_review" if review else "awaiting_user"
        headline = "第" + str(unexported[0]) + "章的配套 Word 还没补齐"
        detail = "上一章正文已验收，但两份 Word 未全部落盘；补齐后才允许继续下一章。"
        primary = resume_action("恢复上次操作补齐两份 Word", "优先补齐原批次导出，不重新生成正文。")
        alternatives.append(_nav("查看已导出的文件", "在「文件」页确认已有的正文与来源/编辑记录。", "文件"))
        return _result(kind, headline, detail, primary, alternatives, avoid, notes)

    if pending is not None:
        candidate_chapter = records.get(pending, {}).get("candidate")
        partial = bool(isinstance(candidate_chapter, dict)) or "仍未写足" in error or "断点" in error
        kind = "needs_review" if review else "awaiting_user"
        headline = ("第" + str(pending) + "章停在断点，接着写就行" if partial else "等你开第" + str(pending) + "章")
        detail = ("第" + str(pending) + "章的部分稿、已写小节和已付费调用都保存在断点里；"
                  "点一次只推进这一章，验收通过后系统会停下等你。")
        primary = _action("next_chapter", ("继续完成第" + str(pending) + "章") if partial else ("研究并写作第" + str(pending) + "章"),
                          "只推进这一章；已写好的小节不重写，只补未完成的断点。",
                          cost=NEW_CALL_NOTE, tab="章节")
        repairable = chapter_repair_actions(state, [pending])
        if repairable:
            alternatives.extend(repairable)
        elif accepted_numbers(state):
            alternatives.append(_action("revise_chapter", "按我的要求修订第" + str(pending) + "章",
                                        "不想继续生成、只想改已写内容时用它；需要写清楚具体范围。",
                                        cost=NEW_CALL_NOTE, requires_feedback=True,
                                        params={"chapter_number": pending}, tab="章节"))
        alternatives.append(_nav("查看候选正文", "在「章节」页预览候选稿和断点位置。", "章节"))
        if len(accepted_numbers(state)) >= 1:
            notes.append("已验收 " + str(len(accepted_numbers(state))) +
                         " 章；系统不会自动往下跑，这是严格的串行与逐章验收设计。")
        return _result(kind, headline, detail, primary, alternatives, avoid, notes)

    # Every planned chapter is accepted: only final assembly is left.
    kind = "needs_review" if review else "awaiting_user"
    if length_actions:
        headline = "字数不在合稿区间，先用预填好的局部修订回到 100000-110000 字"
        detail = ("当前有效正文 " + str(accepted_words(state)) + " 字，合稿要求 " + str(TARGET_MIN) + "-" + str(TARGET_MAX) +
                  " 字。直接再点「全书终审与合稿」会再次被同一条规则拦下，所以这里给的是可直接提交的章节修订。")
        primary = length_actions[0]
        alternatives = alternatives + length_actions[1:]
        avoid.append({"action": "assemble",
                      "reason": "当前 " + str(accepted_words(state)) + " 字仍不在 " + str(TARGET_MIN) + "-" + str(TARGET_MAX) +
                                " 区间，再点一次结果不变"})
        notes.append("修订会重新抽取该章状态并重新过验收门，完成后你再点一次「全书终审与合稿 Word」。")
        return _result(kind, headline, detail, primary, alternatives, avoid, notes)

    if audit_actions:
        headline = "全书终审有高/严重问题，按报告局部修订对应章节"
        detail = "终审报告的问题已按章写入修改要求；修订并重新验收后，本书需要再跑一次合稿。"
        primary = audit_actions[0]
        alternatives = alternatives + audit_actions[1:]
        avoid.append({"action": "assemble", "reason": "终审高/严重问题未消除，直接再合稿会再次被拦下"})
        alternatives.append(_nav("查看终审报告", "在「章节」页查看终审的逐条问题。", "章节"))
        return _result(kind, headline, detail, primary, alternatives, avoid, notes)

    headline = "全部章节已验收，等待全书终审与合稿"
    detail = "核对有效正文总字数、章节、来源与终审问题后生成全书 Word。"
    primary = _action("assemble", "全书终审与合稿 Word",
                      "真实正文覆盖全部章节且字数达标时才会出书；有问题会保留报告并停住。",
                      cost=NEW_CALL_NOTE, tab="文件")
    for number in accepted_numbers(state)[::-1][:3]:
        alternatives.append(_action("revise_chapter", "修订第" + str(number) + "章",
                                    "想在合稿前改某一章时用它；需要写清楚具体范围。",
                                    cost=NEW_CALL_NOTE, requires_feedback=True,
                                    params={"chapter_number": number}, tab="章节"))
    return _result(kind, headline, detail, primary, alternatives, avoid, notes)


def _result(plane: str, headline: str, detail: str, primary: dict[str, Any] | None,
            alternatives: list[dict[str, Any]], avoid: list[dict[str, Any]],
            notes: list[str]) -> dict[str, Any]:
    seen, unique = set(), []
    for item in alternatives:
        signature = (item["action"], str(item["params"]))
        if signature in seen:
            continue
        if primary and item["action"] == primary["action"] and item["params"] == primary["params"]:
            continue
        seen.add(signature)
        unique.append(item)
    return {"stall_kind": plane, "headline": headline, "detail": detail, "primary": primary,
            "alternatives": unique, "avoid": avoid, "notes": [item for item in notes if item]}
