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
- 目标字数体量需要中段足够厚：每 3-5 章一个小推进，每 10-15 章一个大爆点，最后 6-10 章收束主线；如果目标约 20 万字，就按 20 万左右的密度重新安排后续，不要再按 30 万拖长。
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
- 章节必须严格覆盖 expectedChapterNumbers 里的每一个章号，不能缺章、不能多章、不能重复章号。
- 如果 expectedChapterNumbers 是 [11, 12, 13]，chapters 数组就必须恰好有 3 项，章号分别是 11、12、13。

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
- 对白自然，有潜台词，称谓清楚；每次更换说话人必须另起一段，不能把多人对白挤成一串。
- 女主成长循序渐进，不要突然无敌。
- 禁止长篇设定说明，禁止人物突然知道自己不知道的信息。
- 人物不能只剩动作和对白。每个场景的关键转折处，都要写出视角人物从刺激到反应再到选择的连续过程，可用视线停顿、身体反应、记忆触发、欲言又止或对细节的误读来表现，但不要用旁白替人物总结心理。
- 同一人物的说话节奏、回避方式、注意力和利益判断要稳定且有区分度；禁止所有人物使用同一种句式和作者腔。
- 禁止跨场景、跨章节套用同一组“愣住、心里一紧、抿了抿嘴、攥紧手指”式反应模板。人物反应必须来自当下物件、利益、关系和既往处境，而不是通用网文动作库。
- 每个场景都必须让“新信息、利益筹码、人物决定、关系后果”至少一项发生变化。不得换个地点重复同一轮争吵、算账或劝和，不得让同一金额和同一句主张反复承担剧情推进。
- 冲突不能总靠居委会、调解文件、群聊贴条款或旁人主持公道自动解决。解决必须来自人物自己的选择、代价、反制、暴露、失去或关系倒转；坏人闭嘴不等于冲突解决。
- 章节计划要求推进或收束的核心利益必须在正文里有可见结果。房产、拆迁款、借款、养老责任等关键问题不得写到章末仍无交代，除非 currentPlanRecord 明确把它列为下一章钩子。
- 同一人物要有稳定且独有的争取方式。偏袒者会选择性装糊涂，占便宜者会改口径或转嫁代价，回避者会拖延或转移话题，反击者要利用当章已经存在的事实和筹码；不得让所有人轮流复述作者结论。
- 禁止凭空加入新闻报道、网页资料、图片文件名、录音、截图、统计结论、政策原文或新的书面证据。上下文没有的来源和证据一律不存在。
- 控制高频模型腔。不得反复使用“声音不大/很稳”“压低声音”“小声说”“指尖发白”“喉咙发紧”，也不得连续套用“不是……是……”“没有……没有……也没有……”来制造力度。
- 输出前必须逐句静默校对：不造错别字或同音字，不机械替换近义词，不缺主谓宾，不改变人名、称谓、金额、日期和关键事实。
- 全文只用规范中文标点。成对使用“”、‘’、《》和（）；句末标点放在对话引号内；禁止连续堆叠引号、乱码标点、英文直引号和无归属的段首标点。

章节上下文：
{chapter_context}

请直接输出本章正文。
""".strip()


ZHOULI_EDITORIAL_POLISH_PROMPT = """
你是中文长篇现实家庭小说的终稿编辑。下面是一章已经写完的正文。请完成一次整章终稿校对，并直接输出校对后的完整正文，不要解释、不要列清单、不要代码块。

这不是重写任务。章节事件、场景顺序、人物关系、人名、称谓、视角、已知信息、物品归属、金额、日期、数量、因果、立场和结尾钩子都不得改变。不得新增人物、秘密、事实、情节或总结。

必须完成：
1. 逐句修正错别字、同音字、漏字、多字、词语误替换、搭配错误、成分残缺、指代不清和不通顺句子。不要为了显得高级而机械换同义词。
2. 逐处重配中文标点。不同说话人另起一段；“”、‘’、《》、（）必须成对；句末标点按语义放入对话引号；删除连续堆叠引号和乱码标点，但不能机械删光标点。
3. 对只有“动作接对白”的关键转折做局部补足，让人物出现可见的心理过渡：外界刺激、细微反应、利益判断、说出口或没说出口的话、最后选择。只补原情节能够支持的反应，不解释主题，不新增背景；禁止反复套用“愣了一下、心里一紧、抿唇、攥手”等通用反应。
4. 保留人物差异。谁习惯回避、谁爱抢话、谁盯钱、谁顾脸面，要通过各自注意的细节和说话方式显出来，不能让所有角色共用同一腔调。
5. 保持原章篇幅，允许总字数在原稿的 90%-112% 之间。校对完成后再静默通读一遍，确认没有模型说明、病句、错字和标点错位。

章节上下文只用于核对硬事实，不得扩写：
{chapter_context}

待校对正文：
{chapter_text}

请只输出校对后的完整正文。
""".strip()


ZHOULI_NARRATIVE_AUDIT_PROMPT = """
你是中文现实家庭长篇的剧情与人物审稿人。你只诊断，不改写正文。请对照章节上下文，找出会让读者觉得人物扁平、情节公式化、信息稀薄或像模型批量生成的可定位问题。

审查时必须逐项核对：
1. 本章各场景是否真的改变了新信息、利益筹码、人物决定或关系后果；换地点重复同一轮争吵、算账、文件宣读，属于重复而非推进。
2. 人物是否有不同的目标、顾虑、话术和行动策略；若所有人都只复述立场、等居委会/文件/群聊裁决，判为人物扁平和机械解法。
3. 是否有压力递增、选择代价、反制或关系倒转；“把条款发群里后坏人不说话”不能算爽点或解决。
4. 章节计划要求推进或收束的房产、钱款、养老等核心利益是否有可见结果；只有计划明确保留到下一章的内容才可悬置。
5. 是否反复解释同一金额、同一欠款、同一旧情节，或重复上一段后再强行煽情。
6. 是否反复出现“声音不大/很稳、压低声音、小声说、指尖发白、喉咙发紧”，或批量使用“不是……是……”“没有……没有……也没有……”等模型化句式。
7. 是否凭空加入上下文不存在的新闻媒体、网页、图片文件名、录音、截图、报告、统计、政策原文、新人物、新证据或莫名其妙的背景解释。
8. 对白是否符合人物关系和当下利益，是否存在语义荒谬、答非所问、为读者讲设定的长篇说明。

只报告正文里真实存在且能引用证据的问题。不要要求通篇重写，不要把同一问题拆成近义条目，最多 12 条。任何新增事实风险、核心利益断尾、整章无有效推进均为 high 或 severe。

章节上下文：
{chapter_context}

待审正文：
{chapter_text}

输出严格 JSON，不要代码块：
{{
  "approved": false,
  "issues": [
    {{
      "severity": "low|medium|high|severe",
      "issueType": "",
      "evidence": "",
      "requiredFix": ""
    }}
  ],
  "coreStakeStatus": "advanced|resolved|explicitly_deferred|missing",
  "distinctCharacterVoices": true,
  "unsupportedAdditions": []
}}
""".strip()


ZHOULI_NARRATIVE_REPAIR_PROMPT = """
你是中文现实家庭长篇的主修编辑。请根据审稿问题完成一次整章定向修复，并只输出修复后的完整正文。

这是受章节计划约束的修复，不是自由续写：
- 保留章节上下文和原稿已经存在的全部人物、称谓、事实、金额、日期、物品、场景顺序、视角与结尾承接关系；禁止补造新闻、文件、录音、截图、政策、统计、新证据和新背景。
- 删除或合并重复争吵、重复算账、重复旧情节，把腾出的篇幅用于本章计划已经允许的行动、选择、反制和后果，不增加计划外事件。
- 让主要人物用不同策略争取利益，不用作者旁白替他们统一表态。关键转折要有刺激、判断、选择和代价，但不得堆砌身体反应或解释心理结论。
- 不得把居委会、文件、群聊条款写成自动终结冲突的万能按钮；它们最多是筹码，最终变化必须落到人物决定和关系后果上。
- 对章节计划要求收束的核心利益给出明确结果；只有上下文明确列作后续钩子的事项可以延后。
- 去掉反复的模型腔和强行煽情，不机械换同义词，不故意制造病句、错别字、口语残缺或标点错误。
- 修复后总篇幅保持原稿的 90%-112%。所有不同的数字、金额、日期和数量事实必须完整保留，不得改值或新增；同一数字所在整句属于无推进的机械复述时可以合并，但至少保留一次完整事实。

章节上下文：
{chapter_context}

审稿问题：
{audit_json}

原始正文：
{chapter_text}

请只输出修复后的完整正文。
""".strip()


ZHOULI_DEEPSEEK_COPYEDIT_PROMPT = """
你是中文小说终稿校对员。只做文字层面的最后校正，输出完整正文，不解释。

允许修改：错别字、同音误字、漏字、多字、词语误替换、搭配不当、成分残缺、指代不清、对白分段、引号配对、句末标点和明显病句。

禁止修改：人物、称谓、事实、事件、场景顺序、观点、金额、日期、数量、物品归属、因果、立场、结尾钩子和信息量。不得新增新闻、网页、图片文件名、录音、截图、报告、统计、政策、证据、人物或解释。不得机械替换同义词，不把“你”改成“您”，不故意制造人味错误。

额外清理：同一章反复套用“声音不大/很稳、压低声音、小声说、指尖发白、喉咙发紧”时，只在不改变动作事实的前提下删去多余套话；修正连续堆叠的“不是……是……”和“没有……没有……也没有……”，但不得删掉事实。

章节上下文只用于核对，不得从中补写正文：
{chapter_context}

待校正文：
{chapter_text}

请只输出校正后的完整正文。
""".strip()


ZHOULI_TYPOGRAPHY_REPAIR_PROMPT = """
你是中文长篇小说文字校对员。下面的终稿候选仍有可定位的机械问题。只修正问题清单涉及的错字、病句、对白边界、标点、数字锚点或篇幅漂移，不改情节，不换文风，不增加事实，不删除有效内容。

硬要求：
- 保留全部人物、人名、称谓、事件顺序、金额、日期、数量、物品、因果和结尾钩子。
- 不机械替换同义词，不做近义词批量替换，不把“你”改成“您”，不故意制造口语病句、错别字或标点残缺。
- 成对使用中文引号、书名号和括号；不同说话人另起一段；禁止连续引号和无归属标点。
- 输出完整正文，不输出修订说明。

问题清单：
{issues_json}

原始草稿只用于恢复锚点：
{original_text}

当前候选：
{chapter_text}

请只输出修正后的完整正文。
""".strip()


ZHOULI_STATE_EXTRACT_PROMPT = """
你是长篇小说连续性记录员。请从本章正文中抽取状态变化，严格输出 JSON。

这是增量账本，不是正文复述，也不是把旧状态重新抄一遍。必须遵守：
- 只记录本章新发生或发生变化的内容；上下文中已有且本章未变化的人物、物品、事实、矛盾线不得重复输出。
- 不得漏掉会约束后续章节的不可逆变化，包括谁知道了什么、证据/钱款/病历/借条由谁持有、人物承诺、关系破裂、支付与欠款、公开信息和秘密泄露。
- chapterSummary 控制在 120-260 个汉字，只概括本章实际推进。
- events 1-8 项；canonFacts 0-12 项；familyMemberStateDeltas 0-12 项；objectStateChanges 0-10 项；conflictLineUpdates 0-8 项；continuityClaims 0-10 项。
- 同一件事的原因、动作和后果合并为一个 event，不得拆成多条近义记录；字段用短句，禁止粘贴大段正文。
- 本章没有某类变化时输出空数组。不得为了凑数量虚构状态。

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
- 是否存在错别字、同音字误用、漏字、多字、词语误替换、成分残缺、指代不清或明显病句。这些问题按 high 输出。
- 是否存在引号、书名号、括号不成对，标点错位、连续堆叠，不同说话人没有分段，或对白与说话人对不上的情况。这些问题按 high 输出。
- 人物关键选择之前是否完全缺少心理过渡，只剩“动作—对白—动作”；人物是否因腔调、注意点和反应方式高度相同而失去辨识度。只在可定位时输出，不得要求大段心理说明；明显问题按 high 输出。
- 只输出真实存在且可定位的问题；不得复述剧情，不得输出审稿过程，不得把同一个问题拆成多条近义问题。
- issues 最多 10 条，按 severe、high、medium、low 排序。没有问题时必须输出空数组。
- evidence 和 suggestedFix 各用一到两句精确短句；不能为了显得全面而扩写长篇解释。

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
你是长篇小说修稿编辑。请只修复列出的 high/severe 连续性、人物刻画、错别字、病句和标点问题，尽量保持本章原有结构、场景和文风，不要整章推倒重写。

修复原则：
- 称谓、人物关系、物品状态、信息知情范围必须和上下文一致。
- 不新增重生、系统、玄幻、霸总救场、机械降神。
- 不削弱本章生活事件、利益推进和结尾钩子。
- 不机械替换同义词，不改数字、金额、日期、人物立场和既有事实；不同说话人另起一段。
- 修正全部可见错别字、病句和错位标点；“”、‘’、《》、（）必须成对，禁止连续堆叠引号。
- 人物关键反应缺口只做局部补足，用细微动作、感官、犹豫和选择表现，不新增背景，不写解释性心理总结。

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
- 是否有错别字、同音字、病句、指代不清、词语误替换、引号不成对、标点错位和多人对白不分段。任一明显问题都必须写入 problems，typographyScore 或 typoScore 不得给满分。
- 人物是否只有动作和对白而缺少关键心理过渡，人物说话方式、注意点和利益判断是否有辨识度。
- 每个场景是否改变了新信息、利益筹码、人物决定或关系后果；换地点重复争吵、金额和旧情节必须扣分。
- 房产、拆迁款、借款、养老责任等本章核心利益是否实际推进或按计划收束；群聊发条款、居委会调解、坏人沉默不等于解决。
- 人物是否各有目标和争取策略，是否出现反制、代价或关系倒转；所有人轮流复述作者结论属于扁平人物。
- 是否凭空新增媒体报道、网页、图片文件名、录音、截图、统计、政策、新人物或新证据；任一新增都必须列为严重问题。
- 是否反复套用“声音不大/很稳、压低声音、小声说、指尖发白、喉咙发紧”和“不是……是……”“没有……没有……也没有……”等模型句式。

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
  "characterDepthScore": 0,
  "readabilityScore": 0,
  "typographyScore": 0,
  "typoScore": 0,
  "problems": [],
  "rewriteSuggestions": []
}}
""".strip()


def render_prompt(template: str, **kwargs: object) -> str:
    """Render a prompt with the shared genre rules injected."""

    common_rules = f"{COMMON_GENRE_RULES.strip()}\n\n{FAST_PACING_AND_CUSTOM_PLOT_RULES.strip()}"
    return template.format(common_rules=common_rules, **kwargs)
