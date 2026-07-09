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


FAST_PACING_AND_CUSTOM_PLOT_RULES = """
【剧情节奏与自定义路线】
- 开章要更快进入矛盾：本章前 300-500 字内必须出现具体家庭利益、情绪压迫、信息差、账目证据或关系冲突，不能先铺大段背景。
- 每一章都要有实际推进：至少出现一个“信息变化、证据变化、利益位置变化、人物关系变化、情绪立场变化、物品/账目归属变化”。
- 每个场景都必须带着目的推进，不要只写聊天、回忆、解释设定或重复委屈；没有推进的寒暄和铺垫要压缩。
- 章节结尾必须留出下一章可接的钩子：新证据、新质问、新账目、新人证、新误会、新利益交换或一次关系翻脸。
- 如果项目输入里的 sourceType 是 custom_plot，sourceText 就是用户指定的后续剧情路线、爆点和关系变化。它不是普通素材，要作为后续圣经、大纲、分章和正文的硬方向执行；只有与已完成连续性冲突时才做小幅顺延。
- 自定义剧情路线只能影响未生成的后续章节，不能推翻已完成章节、已登记人物状态、物品状态和已解决矛盾。
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


ZHOULI_30W_CONTINUATION_REPLAN_PROMPT = """
你是现有长篇小说工程的总编剧和连续性规划器。现在不是新开一本书，也不是人工续写几章，而是把一本已经完成前文的家长里短长篇，基于现有正文和状态，重新升级为目标字数的完整长篇项目。

{common_rules}

核心任务：
- 只规划后续，不改写已完成章节正文。
- 必须把已完成章节、已登记人物状态、物品状态、事实、矛盾线、关系网和作品圣经全部当成硬事实。
- 必须像重新规划一个目标字数项目一样，升级作品圣经、后续终局、分卷大纲、矛盾线时间表、人物关系推进、未来章节计划。
- 后续章节必须承接前文已经发生的剧情，不能把人物、物品、矛盾、账目、亲戚关系、上一章钩子忘掉或推翻。
- 可以新增后续人物、后续矛盾线、后续证据物、后续关系变化，但必须从前文已经存在的家庭结构、账目、物品、关系或事件中自然长出来。
- 不能使用固定模板，不能让多个章节只换标题但目标、冲突、钩子相同。
- 不要生成正文，只输出规划 JSON。

规划原则：
- 已完成章节是地基，后续所有章节都必须解释为“从这些地基上长出来”。
- 30 万字体量需要中段足够厚：每 3-5 章一个小推进，每 10-15 章一个大爆点，最后 6-10 章收束主线。
- 每章必须至少有一个具体变化：信息变化、证据变化、利益位置变化、人物站队变化、关系变化、物品状态变化或情绪边界变化。
- 结局不能原地收掉，必须把终局延后，让账、责任、边界、关系和代价逐步落地。
- 如果旧大纲、旧结局或旧章节计划太短，要明确在 biblePatch/targetExpansion 中写清楚如何延后和扩容。

扩容上下文：
{expansion_context}

输出严格 JSON，不要代码块：
{{
  "biblePatch": {{
    "theme": "",
    "corePromise": "",
    "mainConflict": "",
    "emotionalHook": "",
    "familyBackground": "",
    "endingDirection": "",
    "worldRules": [],
    "styleRules": [],
    "forbiddenRules": [],
    "targetExpansion": {{
      "lockedCompletedChapters": 0,
      "continuityPrinciples": [],
      "futureVolumeArcs": [
        {{
          "volumeNumber": 2,
          "title": "",
          "startChapter": 11,
          "endChapter": 22,
          "goal": "",
          "majorExplosion": "",
          "mustCarryFromCompletedChapters": []
        }}
      ]
    }}
  }},
  "familyMembers": [
    {{
      "name": "",
      "familyRole": "",
      "publicImage": "",
      "privateDesire": "",
      "grievance": "",
      "leverage": "",
      "weakness": "",
      "secret": "",
      "relationshipToHeroine": "",
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
      "currentTensionLevel": 5
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
      "plannedResolutionChapter": 60,
      "status": "open",
      "importance": "core"
    }}
  ],
  "chapters": [
    {{
      "chapterNumber": 11,
      "volumeNumber": 2,
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
  ],
  "qualitySelfCheck": {{
    "completedChaptersPreserved": true,
    "futureChapterCoverage": "",
    "continuityRisks": [],
    "whyThisStillFeelsLikeOneNovel": ""
  }}
}}
""".strip()


ZHOULI_30W_GLOBAL_REPLAN_PROMPT = """
你是现有家长里短长篇小说工程的总编剧和连续性规划器。现在要把一部已经完成前文的小说，承接已有正文和状态，升级为目标字数的完整长篇项目。

{common_rules}

核心任务：
- 只做全局承接规划，不生成正文，不生成逐章列表。
- 已完成章节、已登记人物状态、物品状态、事实、矛盾线、关系网和作品圣经都是硬事实，不能推翻。
- 必须像重开一个目标字数项目一样，升级作品圣经、后续终局、分卷推进、矛盾线时间表、人物关系推进策略。
- 可以新增后续人物、后续矛盾线、后续证据物、后续关系变化，但必须从前文已有家庭结构、账目、物品、关系或事件里自然长出来。
- 不能使用固定模板，不能空泛写“继续发酵”“逐步推进”。

扩容上下文：
{expansion_context}

输出严格 JSON，不要代码块：
{{
  "biblePatch": {{
    "theme": "",
    "corePromise": "",
    "mainConflict": "",
    "emotionalHook": "",
    "familyBackground": "",
    "endingDirection": "",
    "worldRules": [],
    "styleRules": [],
    "forbiddenRules": [],
    "targetExpansion": {{
      "lockedCompletedChapters": 0,
      "continuityPrinciples": [],
      "futureVolumeArcs": [
        {{
          "volumeNumber": 2,
          "title": "",
          "startChapter": 11,
          "endChapter": 22,
          "goal": "",
          "majorExplosion": "",
          "mustCarryFromCompletedChapters": []
        }}
      ]
    }}
  }},
  "familyMembers": [],
  "familyRelations": [],
  "conflictLines": [
    {{
      "conflictKey": "",
      "name": "",
      "conflictType": "",
      "description": "",
      "introducedChapter": 1,
      "plannedExplosionChapter": 20,
      "plannedResolutionChapter": 60,
      "status": "open",
      "importance": "core"
    }}
  ],
  "continuationStrategy": {{
    "whatMustBePreservedFromCompletedChapters": [],
    "newMidgamePressure": [],
    "lateGamePayoff": [],
    "antiRepetitionRules": []
  }},
  "qualitySelfCheck": {{
    "completedChaptersPreserved": true,
    "whyThisStillFeelsLikeOneNovel": ""
  }}
}}
""".strip()


ZHOULI_30W_CHAPTER_BATCH_REPLAN_PROMPT = """
你是现有家长里短长篇小说工程的分章规划器。现在根据全局续规划，为指定章节区间生成逐章计划。

{common_rules}

硬规则：
- 只规划本批章节，不生成正文。
- 必须承接已完成章节和前面已经规划的未来章节。
- 本批每章都必须有独立的具体家庭事件、具体证据/物品/账目变化、人物站队变化或利益位置变化。
- 不允许多个章节只换标题但 chapterGoal、domesticConflict、endingHook 近似重复。
- 不允许提前推翻已完成章节，不允许提前解决终局矛盾。
- 章节必须覆盖 batchStart 到 batchEnd 的每一章，不能缺章、不能多章、不能重复章号。

分批上下文：
{batch_context}

输出严格 JSON，不要代码块：
{{
  "chapters": [
    {{
      "chapterNumber": 11,
      "volumeNumber": 2,
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
  ],
  "batchSelfCheck": {{
    "coverage": "",
    "noRepeatedChapterCore": true,
    "continuityWithPreviousPlans": ""
  }}
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
- 是否开头过慢、铺垫过多、整章只有情绪重复没有新的信息/证据/利益推进；若存在，应在 problems 中明确指出。

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

    common_rules = f"{COMMON_GENRE_RULES.strip()}\n\n{FAST_PACING_AND_CUSTOM_PLOT_RULES.strip()}"
    return template.format(common_rules=common_rules, **kwargs)
