"""步骤 3 自检: prompt 完整性 + count_words + 5000字闭环 (mock LLM)。"""
import sys
import io

# Windows 控制台 UTF-8
if sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

import prompt_templates as pt
import services as svc
from services import count_words, generate_chapter

# ====== 1. prompt 模板字面校验 (一字不差) ======
assert "你是一个拥有十年经验的网文金牌主编" in pt.OUTLINE_SYSTEM_PROMPT
assert "{video_text}" in pt.OUTLINE_USER_TEMPLATE
assert "至少 4 个紧密相连的子场景(sub_scenes)" in pt.OUTLINE_USER_TEMPLATE

assert pt.POSTER_SYSTEM_PROMPT.strip() == "你是一个电影美术指导。"
assert "{outline_summary}" in pt.POSTER_USER_TEMPLATE
assert "只返回纯英文描述词" in pt.POSTER_USER_TEMPLATE

assert "白金级网文大神" in pt.WRITING_SYSTEM_PROMPT
assert "Show, don" in pt.WRITING_SYSTEM_PROMPT
for ph in ("{global_setting}", "{previous_context}", "{current_sub_scene}"):
    assert ph in pt.WRITING_USER_TEMPLATE, f"WRITING 缺占位 {ph}"
assert "车轱辘话" in pt.WRITING_USER_TEMPLATE

for ph in (
    "{global_setting}", "{tail_context}", "{tail_window}",
    "{current_words}", "{gap_words}",
):
    assert ph in pt.PATCH_USER_TEMPLATE, f"PATCH 缺占位 {ph}"
print("[OK] 1. prompt 模板字面与占位符完整")

# ====== 2. 字数统计 ======
assert count_words("") == 0
assert count_words("你好世界") == 4
assert count_words("Hello World 123") == 3
# 混合 (2 CJK) + abc (1 token) + 中文 (2 CJK) + 123 (1 token) = 6
assert count_words("混合abc中文123！") == 6
assert count_words("a b c d") == 4
print("[OK] 2. count_words 中英文/数字混合统计正确")

# ====== 3. 5000 字闭环 (mock _chat) ======
chapter = {
    "chapter_num": 1,
    "title": "风暴前夜",
    "core_conflict": "主角发现真相却无法说出口",
    "sub_scenes": [f"子场景{i}" for i in range(1, 5)],   # 4 个
}

# 3a. 子场景每次 ~1000 字 -> 4 段 ~4000 字 -> 必然触发补丁
call_log: list[dict] = []

def fake_short(system_prompt, user_prompt, *, temperature, json_mode=False, max_tokens=None):
    is_patch = "字数缺口" in user_prompt
    call_log.append({"is_patch": is_patch, "temp": temperature})
    if is_patch:
        return "环境的雾气在此刻凝固。" * 80   # ~10字 * 80 = 800 字
    return "夜色深沉的描写细节。" * 100        # ~10字 * 100 = 1000 字

svc._chat = fake_short
text, words = generate_chapter("全局设定占位", chapter)
sub_calls = sum(1 for c in call_log if not c["is_patch"])
patch_calls = sum(1 for c in call_log if c["is_patch"])
print(f"[OK] 3a. 短场景: 子场景调用 {sub_calls} 次 + 补丁 {patch_calls} 次 -> 最终 {words} 字")
assert sub_calls == 4, f"子场景应=4, 实际 {sub_calls}"
assert patch_calls >= 1, "字数不足必须触发至少 1 次补丁"
assert words >= svc.settings.CHAPTER_MIN_WORDS or patch_calls == svc.settings.MAX_PATCH_ROUNDS, \
    "应达标或耗尽补丁轮次"

# 3b. 子场景已高产 -> 不触发补丁
call_log.clear()
def fake_long(system_prompt, user_prompt, *, temperature, json_mode=False, max_tokens=None):
    is_patch = "字数缺口" in user_prompt
    call_log.append({"is_patch": is_patch})
    if is_patch:
        raise RuntimeError("高产场景不应触发补丁!")
    return "深度场景描写细致铺陈。" * 200   # ~10 * 200 = 2000 字

svc._chat = fake_long
text2, w2 = generate_chapter("全局", chapter)
print(f"[OK] 3b. 高产场景: {w2} 字, 共调 {len(call_log)} 次 (无补丁)")
assert len(call_log) == 4
assert w2 >= svc.settings.CHAPTER_MIN_WORDS

# 3c. 补丁返回空 -> 防死循环
call_log.clear()
def fake_empty_patch(system_prompt, user_prompt, *, temperature, json_mode=False, max_tokens=None):
    is_patch = "字数缺口" in user_prompt
    call_log.append({"is_patch": is_patch})
    if is_patch:
        return ""   # 空补丁
    return "短文" * 50   # 4 段共 ~400 字 -> 必触发补丁

svc._chat = fake_empty_patch
text3, w3 = generate_chapter("全局", chapter)
print(f"[OK] 3c. 补丁空返回防御: {w3} 字, 共调 {len(call_log)} 次 (4 子场景 + 1 空补丁后中止)")
assert len(call_log) == 5, f"应在首次空补丁后中止, 实际 {len(call_log)}"

# 3d. 补丁有效但字数无增长 -> 防死循环
call_log.clear()
def fake_zero_growth(system_prompt, user_prompt, *, temperature, json_mode=False, max_tokens=None):
    is_patch = "字数缺口" in user_prompt
    call_log.append({"is_patch": is_patch})
    if is_patch:
        return "！？"   # 全是标点, count_words 返回 0
    return "短" * 50
svc._chat = fake_zero_growth
text4, w4 = generate_chapter("全局", chapter)
print(f"[OK] 3d. 补丁零增长防御: {w4} 字, 共调 {len(call_log)} 次")
assert len(call_log) == 5

# 3e. 子场景为空 -> 必须抛错
try:
    generate_chapter("全局", {"chapter_num": 9, "title": "空", "core_conflict": "", "sub_scenes": []})
    raise AssertionError("空 sub_scenes 应抛 ValueError")
except ValueError as e:
    print(f"[OK] 3e. 空 sub_scenes 防御: {e}")

# ====== 4. 海报摘要 + global_setting 构造 ======
fake_outline = {
    "world_building": "近未来都市，AI 主导一切。",
    "character_profiles": [{"name": "陈默", "core": "黑客"}],
    "chapters": [
        {"chapter_num": 1, "title": "暗流", "core_conflict": "目击谋杀"},
        {"chapter_num": 2, "title": "追猎", "core_conflict": "被追杀"},
    ],
}
poster_summary = svc.summarize_outline_for_poster(fake_outline)
gs = svc.build_global_setting(fake_outline)
assert "近未来都市" in poster_summary
assert "陈默" in gs
print(f"[OK] 4. summarize_outline_for_poster / build_global_setting OK")

print()
print("=" * 60)
print("  步骤 3 自检 全部通过 ✅  5000 字闭环已闭合")
print("=" * 60)
