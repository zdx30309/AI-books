执行全书终审，只输出 JSON：{"issues":[{"severity":"low/medium/high/severe","type":"coverage/length/fact/repetition/continuity","description":"具体章/小节位置与问题","suggested_fix":"局部修订位置"}],"summary":"终审结论"}。
必须核对全部真实已验收章节、原定任务、来源、术语、时间线及跨章语义重复。不能拿摘要补缺章，不能用正文以外的包装、目录、来源、记录凑 100000 字。高/严重问题阻断全书完成；只提出局部修复，不输出另一份全书重写稿。不要声称尚未执行的 Word 检查已完成。

同一 JSON 中必须增加 style_review，不能只照抄章节旧审核，需重读 context.chapters 的实际全文、按全书相邻与跨章语境复核：
{"style_review":{"positive_application":[{"chapter_number":1,"card_id":"已批准positive卡id","evidence":"该章实际正文的连续片段","reason":"信息推进、参与者因果或自然节奏的具体表现"}],"negative_checks":[{"chapter_number":1,"card_id":"已批准negative卡id","evidence":"该章实际正文的连续片段","finding":"clear或problem","reason":"模板表达、同义解释或机械收束的排查结论"}],"ordinary_word_review":{"mechanical_ban_applied":false,"reason":"按语境而非机械禁词评估普通词","examples":[{"chapter_number":1,"evidence":"该章实际正文的连续片段","reason":"该处的句子功能与信息增量"}]},"historical_reports_used_as_current_result":false}}。
上述三份清单都要覆盖全部实际章号，每章每类至少一条证据，按需要引用卡片而非照搬所有卡。证据 8—240 字符，逐字来自对应章正文且不是标题。角色引用与 context.reference 一致。problem、缺章证据、正反颠倒、机械禁词或把历史样稿报告当成当前结果均阻断通过。发现问题定位对应章局部修订，不强行改写全书，不生成或承诺任何新稿检测分数。
