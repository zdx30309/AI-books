# 社科2610实施计划

仅替换社会痛点测试版的新建及明确重新生成任务。保留类别ID与旧任务固定工作流，不修改其他普通书稿、长篇、封面及漫画。

1. 原样保存用户Word提示词，记录SHA256；只替换raw_text占位符。
2. 专属单次Responses调用gpt-6-astra，reasoning.mode=pro；复用客户端和并发限制，禁止隐式重试、润色、格式化及自动改写。记录状态、用量、估算费用与错误；不确定请求暂停，成功缓存不重复调用。
3. 新任务及明确重新生成任务固定新工作流，旧任务继续旧流程。原样保存模型输出，解析器兼容#结构标记，预览/MD/Word不重写正文。
4. 修改类别名称和流程展示，移除该类别的额外文风输入。离线测试路由、请求参数、失败续跑、章节入库和导出；构建前端。
5. 提交个人Git仓库，备份并部署服务器，核对运行文件、服务健康及历史数据，不为验收触发付费生成。

官方接口定义：https://developers.openai.com/api/docs/guides/reasoning#reasoning-mode 。只读models.retrieve已确认gpt-6-astra存在，尚未发起Pro推理。

Word正文提示词SHA256：acfbc7e47cda2b94d3c0a022f6d186f6b84ea485a9ed1bb24c40192dc9a7ade3
