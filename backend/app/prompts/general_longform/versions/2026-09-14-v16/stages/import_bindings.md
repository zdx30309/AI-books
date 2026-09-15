为已经恢复的外部大纲重新绑定本次真实读取的来源。只核对事实依据，不改书名、章名、小节名、任务、观点、字数、结构和确认状态。
输出 JSON：{"chapters":[{"number":1,"source_ids":["实际来源id"],"sections":[{"title":"原小节名","source_ids":["实际来源id"],"reason":"该来源具体支持哪些任务"}]}],"gaps":[]}。
按原顺序完整覆盖全部章和小节，只使用 context.sources 中有 page_read_and_abstracted 证据的 id。逐项判断真实材料是否支撑任务，不能为了填满来源字段随意绑定。不足的内容在 gaps 中明确列出；不要修改原大纲来伪装有依据。
