# OpenAI 401 鉴权故障与通用长篇恢复

## 2026-09-15 现场定位

- 北京 ECS 的实际 worker 使用既有代理访问 OpenAI；只读 `GET /v1/models` 在 3.35 秒返回 HTTP 401、`account_deactivated`。
- 本地活动配置与线上 worker 的密钥指纹相同。这不是凭据漏部署，也不是代理连接超时；OpenAI 拒绝的是当前密钥所属账户。
- 故障发生在通用长篇的参考机制抽象阶段，尚无大纲、正文或 OpenAI response ID。旧错误包装丢失 `status_code`，导致工作区误提示“连接失败”和“交付待定”。
- 软件修复不等于账户已恢复。实际写作恢复仍要求有效账户的 `OPENAI_API_KEY`；代理、模型和 prompt 不需要为这次鉴权错误而更换。

## 修复边界

1. 共用主笔保留明确拒绝的 HTTP 状态、错误代码和 request ID，给出针对鉴权/权限/参数的提示。
2. 无 response ID 的明确鉴权拒绝记录为 `failed` / `rejected_before_generation`，不要求用户承担“原调用交付未知”的确认。
3. 已有 response ID 时保持原响应恢复；HTTP 408、传输中断和明确交付待定仍受原有防重复计费保护。
4. 旧任务只在备份、核对原始错误和无活动执行后纠正错误分类；不删稿、不改 prompt、不重发生成。

## 检查与恢复

在北京 ECS 的 `/opt/video-plan` 下执行只读检查：

```sh
cd /opt/video-plan
docker compose exec -T worker python - < scripts/check_openai_auth.py
docker compose exec -T backend python - < scripts/check_openai_auth.py
```

该工具只请求模型列表，显示状态码、错误代码和不可逆密钥指纹，不打印密钥或代理凭据，不发送生成请求。HTTP 200 只证明鉴权检查通过，不代表已完成书稿生成。

如仍返回 `account_deactivated`，由账户管理员处理账户状态，或在项目的 `/opt/video-plan/.env` 中配置有效账户密钥。密钥不要进入聊天、Git 或日志。仅修改 `.env` 后，旧容器仍可能继续使用启动时的旧环境；确认没有活动任务、Celery 待处理消息或未确认消息后，再重新创建使用该环境的服务：

```sh
cd /opt/video-plan
docker compose up -d --no-build --no-deps --force-recreate backend worker maintenance beat
docker compose exec -T worker python - < scripts/check_openai_auth.py
docker compose exec -T backend python - < scripts/check_openai_auth.py
```

两个服务检查通过后，在原书稿工作区点击“恢复上次操作”。保留原任务、原请求和已完成材料，不另建重复书稿；这次正常恢复可能开始新的有效模型调用。

官方参考：[OpenAI API 错误码说明](https://developers.openai.com/api/docs/guides/error-codes)。

## 验收记录

- 定向初验：63 项通过，包含主笔拒绝类型、原 prompt 恢复、已有 response ID、未知交付保护、API 鉴权及用户显式恢复路径。
- 完整回归：498 项通过（66.44 秒，仅一条已有 Google SDK 弃用警告）；Python 编译、diff 检查和 v02 的 840 段 / 23 分段完整性检查通过。没有修改前端 bundle 或提示词。
- 部署与现有任务纠正：待执行。
- 账户真实可用性：现场只读检查仍为 `401 account_deactivated`，不声明已恢复实际生成。
