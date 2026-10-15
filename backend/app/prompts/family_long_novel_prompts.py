"""Dedicated prompts for family sisters-in-law long-form novels."""

FAMILY_LONG_NOVEL_TYPE = "family_sisters_in_law_longform"
FAMILY_LONG_NOVEL_LABEL = "家长里短·妯娌"


COMMON_GENRE_RULES = """
你正在生成的是家长里短、妯娌、家庭矛盾长篇小说，不是玄幻、重生、系统、霸总、悬浮爽文。
故事必须围绕真实家庭利益和情绪压迫展开：房子、钱、孩子、老人、面子、彩礼、嫁妆、带娃、住院、养老、拆迁、借钱、分家、过年、酒席、亲戚议论、兄弟偏心、婆婆双标、丈夫愚孝。

核心口味：
- 真实、憋屈、有代入感，读者要觉得“这事我见过”“这家人太偏心”“我想看女主怎么反击”。
- 反派不能脸谱化，婆婆偏心、妯娌算计、丈夫愚孝都要有现实逻辑和代价。
- 女主不能开局无敌，要从忍耐、委屈、试图讲理，逐步走向清醒和反击。
- 爽感来自证据、账目、真相、权力结构改变，不来自开挂和机械降神。

硬性禁忌：
- 禁止重生、系统、玄幻、霸总救场、突然中彩票、超级富豪、机械降神。
- 禁止所有人突然悔悟，禁止大段说教，禁止无意义撕逼，禁止低俗擦边。
- 禁止美化违法报复，禁止过度渲染家暴细节。
- 禁止人物称谓混乱，尤其不能让大嫂、二嫂、弟媳、婆婆、公公、儿子、媳妇关系错位。
- 禁止让角色提前知道未公开信息，已解决的矛盾线不能重新当成新矛盾。
"""


ZHOULI_BIBLE_PROMPT = """
你是成熟的家长里短妯娌长篇小说总编剧。请根据项目输入，生成一份专属作品圣经。

{common_rules}

项目输入：
{project_payload}

输出必须是严格 JSON，不要代码块，不要解释。字段必须完整：
{{
  "title": "",
  "genre": "家长里短·妯娌",
  "theme": "",
  "corePromise": "",
  "emotionalHook": "",
  "familyBackground": "",
  "mainConflict": "",
  "endingDirection": "",
  "worldRules": [],
  "styleRules": [],
  "forbiddenRules": [],
  "familyMembers": [
    {{
      "name": "",
      "familyRole": "",
      "age": "",
      "publicImage": "",
      "privateDesire": "",
      "grievance": "",
      "leverage": "",
      "weakness": "",
      "secret": "",
      "relationshipToHeroine": "",
      "relationshipMap": {{}},
      "voiceRules": {{}},
      "arcStart": "",
      "arcEnd": "",
      "ending": ""
    }}
  ],
  "familyRelations": [
    {{
      "fromMember": "",
      "toMember": "",
      "relationType": "",
      "surfaceRelation": "",
      "hiddenConflict": "",
      "interestConflict": "",
      "emotionalDebt": "",
      "currentTensionLevel": 1
    }}
  ],
  "conflictLines": [
    {{
      "conflictKey": "",
      "name": "",
      "conflictType": "",
      "description": "",
      "introducedChapter": 1,
      "plannedExplosionChapter": 20,
      "plannedResolutionChapter": 45,
      "importance": "major"
    }}
  ]
}}
""".strip()


ZHOULI_FAMILY_MAP_PROMPT = """
你是家族关系与利益冲突架构师。请基于作品圣经，补全家庭关系图、利益关系图、矛盾关系图。

{common_rules}

作品圣经：
{bible_json}

输出严格 JSON，不要代码块：
{{
  "familyMembers": [],
  "familyRelations": [],
  "conflictLines": [],
  "interestMap": [],
  "tensionMap": []
}}
""".strip()


ZHOULI_OUTLINE_PROMPT = """
你是长篇家长里短小说结构编辑。请严格按项目目标章节数生成大纲，并按篇幅组织成合理分卷。

{common_rules}

要求：
- 章节数尽量贴合项目目标章节数。
- 每章 targetWords 必须统一使用项目输入里的 averageChapterTargetWords，不要让章节字数忽高忽低。
- 短篇测试项目也必须完整成书：每 3-5 章有一个小爆点，长篇项目每 10-15 章有一个大爆点。
- 中段必须有关系反转，后段必须揭开利益真相，结尾必须让家庭结构重新洗牌。
- 每章至少推进一条 conflict line，并且必须有情绪钩子。
- 不要一路撕逼却没有推进，不要每章都是同一个模板。
- 所有章节标题 10 个汉字以内，清楚、简洁，禁止使用“把”字句、“被”字句等复杂句式。

项目输入：
{project_payload}

作品圣经：
{bible_json}

输出严格 JSON：
{{
  "volumes": [
    {{
      "volumeNumber": 1,
      "title": "",
      "goal": "",
      "startChapter": 1,
      "endChapter": 12,
      "emotionalStage": ""
    }}
  ],
  "chapters": [
    {{
      "chapterNumber": 1,
      "volumeNumber": 1,
      "title": "",
      "targetWords": 4000,
      "chapterGoal": "",
      "domesticConflict": "",
      "emotionalHook": "",
      "requiredEvents": [],
      "characterArcMove": [],
      "conflictLinesToAdvance": [],
      "forbiddenReveals": [],
      "endingHook": ""
    }}
  ]
}}
""".strip()


ZHOULI_CHAPTER_PLAN_PROMPT = """
你是妯娌长篇小说分章导演。请为指定章节生成 3-5 个可执行场景。

{common_rules}

上下文只允许使用当前结构化状态，不要臆造已经发生但状态里不存在的大事件。

章节上下文：
{chapter_context}

输出严格 JSON：
{{
  "chapterNumber": 1,
  "title": "",
  "chapterGoal": "",
  "mainDomesticConflict": "",
  "emotionalHook": "",
  "scenes": [
    {{
      "sceneNumber": 1,
      "sceneGoal": "",
      "location": "",
      "characters": [],
      "domesticConflict": "",
      "subtext": "",
      "informationChange": "",
      "emotionalTurn": "",
      "endingBeat": ""
    }}
  ],
  "mustInclude": [],
  "mustNotReveal": [],
  "styleNotes": []
}}
""".strip()


ZHOULI_CHAPTER_WRITE_PROMPT = """
你是成熟的中文长篇家长里短小说作者。请根据章节计划直接写本章正文，不要输出 JSON，不要解释，不要代码块。

{common_rules}

正文要求：
- 中文，字数必须贴合章节上下文 currentPlanRecord.targetWords，允许上下浮动 8%，不要写成章节梗概，不要随意缩短。
- 3-5 个生活场景，每个场景都要有具体事件，不要空谈情绪。
- 冲突必须具体，至少推进一条 conflict line。
- 至少产生一个不可逆家庭影响。
- 结尾必须有 hook，让读者想继续读下一章。
- 对白自然，有潜台词，称谓清楚。
- 女主成长循序渐进，不要突然无敌。
- 禁止长篇设定说明，禁止人物突然知道自己不知道的信息。

章节上下文：
{chapter_context}

请直接输出本章正文。
""".strip()


ZHOULI_STATE_EXTRACT_PROMPT = """
你是长篇小说连续性记录员。请从本章正文中抽取状态变化，严格输出 JSON。

章节上下文：
{chapter_context}

本章正文：
{chapter_text}

输出严格 JSON：
{{
  "chapterSummary": "",
  "events": [
    {{
      "eventType": "",
      "actors": [],
      "location": "",
      "timeLabel": "",
      "cause": "",
      "effect": "",
      "familyImpact": ""
    }}
  ],
  "canonFacts": [
    {{
      "fact": "",
      "factType": "",
      "entities": [],
      "confidence": 1
    }}
  ],
  "familyMemberStateDeltas": [
    {{
      "memberName": "",
      "goal": "",
      "emotion": "",
      "grievanceLevel": 1,
      "currentLeverage": "",
      "knowledgeAdded": [],
      "secretRevealed": [],
      "relationshipTensionDelta": {{}}
    }}
  ],
  "objectStateChanges": [
    {{
      "objectName": "",
      "holder": "",
      "location": "",
      "knownBy": [],
      "status": ""
    }}
  ],
  "conflictLineUpdates": [
    {{
      "conflictKey": "",
      "updateType": "introduced|advanced|exploded|resolved",
      "description": ""
    }}
  ],
  "continuityClaims": []
}}
""".strip()


ZHOULI_CONTINUITY_CHECK_PROMPT = """
你是长篇小说连续性审校。请检查本章是否破坏家庭关系、物品状态、信息知情范围和矛盾线推进。

{common_rules}

需要重点检查：
- 家庭成员称谓是否混乱。
- 大嫂、二嫂、弟媳、婆婆、公公、儿子、媳妇关系是否错位。
- 已经知道的信息不能重复当作第一次知道，未知道的信息不能提前说出。
- 房产证、存折、借条、病历、拆迁协议等关键物品不能瞬移。
- 已爆发或已解决的矛盾线不能又当成新矛盾。
- 每章是否推进至少一条 conflict line，是否有不可逆家庭影响，结尾是否有 hook。
- 是否违反禁忌和 forbiddenRules。

章节上下文：
{chapter_context}

本章正文：
{chapter_text}

输出严格 JSON：
{{
  "issues": [
    {{
      "severity": "low|medium|high|severe",
      "issueType": "",
      "description": "",
      "evidence": "",
      "suggestedFix": ""
    }}
  ]
}}
""".strip()


ZHOULI_REPAIR_PROMPT = """
你是长篇小说修稿编辑。请只修复列出的 high/severe 连续性问题，尽量保持本章原有结构、场景和文风，不要整章推倒重写。

修复原则：
- 称谓、人物关系、物品状态、信息知情范围必须和上下文一致。
- 不新增重生、系统、玄幻、霸总救场、机械降神。
- 不削弱本章生活事件、利益推进和结尾钩子。

章节上下文：
{chapter_context}

需要修复的问题：
{issues_json}

原正文：
{chapter_text}

请直接输出修复后的完整本章正文。
""".strip()


ZHOULI_QUALITY_CHECK_PROMPT = """
你是家长里短妯娌小说质量审稿人。请检查本章是否符合读者口味，严格输出 JSON。

评分重点：
- 是否像家长里短妯娌文。
- 是否有具体生活矛盾和真实利益冲突。
- 是否有憋屈感、爽感和结尾钩子。
- 是否推动冲突线，称谓是否清楚，人物动机是否合理。

章节上下文：
{chapter_context}

本章正文：
{chapter_text}

输出严格 JSON：
{{
  "score": 0,
  "genreFitScore": 0,
  "domesticConflictScore": 0,
  "emotionalHookScore": 0,
  "characterConsistencyScore": 0,
  "readabilityScore": 0,
  "problems": [],
  "rewriteSuggestions": []
}}
""".strip()


def render_prompt(template: str, **kwargs: object) -> str:
    """Render a prompt with the shared genre rules injected."""

    return template.format(common_rules=COMMON_GENRE_RULES.strip(), **kwargs)
