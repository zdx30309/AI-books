"""步骤 5 自检: schemas + FastAPI app 端点 / CORS / OpenAPI。"""
import sys
import io

if sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

# 屏蔽 init_db 启动失败的告警 (本机没真实 DB)
import logging
logging.basicConfig(level=logging.ERROR)

from fastapi.testclient import TestClient

import main as main_mod
from main import app

# === 1. /health & / ===
with TestClient(app) as client:
    r = client.get("/health")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert body["app"] == "GravitySingularity"
    print(f"[OK] 1. GET /health -> {body}")

    r = client.get("/")
    assert r.status_code == 200
    print(f"[OK] 1b. GET / -> 200")

    # === 2. OpenAPI / 路由完备 ===
    r = client.get("/openapi.json")
    assert r.status_code == 200
    spec = r.json()
    paths = set(spec["paths"].keys())
    expected = {
        "/",
        "/health",
        "/api/v1/tasks",
        "/api/v1/tasks/{task_id}",
        "/api/v1/tasks/{task_id}/chapters",
    }
    missing = expected - paths
    assert not missing, f"路由缺失: {missing}"
    print(f"[OK] 2. OpenAPI 注册了全部 {len(expected)} 个路由")

    # === 3. CORS preflight ===
    r = client.options(
        "/api/v1/tasks",
        headers={
            "Origin": "https://demo.example.com",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type",
        },
    )
    assert r.status_code in (200, 204), r.status_code
    allow_origin = r.headers.get("access-control-allow-origin")
    allow_methods = r.headers.get("access-control-allow-methods", "")
    assert allow_origin in ("*", "https://demo.example.com"), allow_origin
    print(f"[OK] 3. CORS preflight -> origin={allow_origin}, methods={allow_methods}")

    # === 4. POST 请求体校验 (空 url 应 422) ===
    r = client.post("/api/v1/tasks", json={"url": "", "config": {}})
    assert r.status_code == 422, r.status_code
    print(f"[OK] 4. POST 空 url 触发 422 校验失败 (Pydantic 生效)")

    # === 5. GET 不存在的 UUID -> 404 (mock get_db) ===
    from database import get_db
    from unittest.mock import MagicMock

    fake_db = MagicMock()
    fake_db.get.return_value = None      # task 找不到
    fake_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []

    def fake_get_db():
        yield fake_db

    app.dependency_overrides[get_db] = fake_get_db
    try:
        fake_uuid = "00000000-0000-0000-0000-000000000001"
        r = client.get(f"/api/v1/tasks/{fake_uuid}")
        assert r.status_code == 404, r.status_code
        assert "not found" in r.json()["detail"]
        print(f"[OK] 5. GET 不存在 task -> 404 ({r.json()['detail']})")

        r = client.get(f"/api/v1/tasks/{fake_uuid}/chapters")
        assert r.status_code == 404
        print(f"[OK] 5b. GET chapters 不存在 task -> 404")

        # === 6. UUID 格式错 -> 422 ===
        r = client.get("/api/v1/tasks/not-a-uuid")
        assert r.status_code == 422
        print(f"[OK] 6. GET 非法 UUID -> 422 (FastAPI 路径参数校验)")

        # === 7. POST 成功路径 (mock task 创建 + Celery 投递) ===
        import uuid
        from datetime import datetime, timezone
        from models import Task, TaskStatus

        created_task = Task(
            id=uuid.uuid4(),
            source_url="https://example.com/v",
            status=TaskStatus.PENDING,
            progress=0,
            current_step="排队中",
            config={},
        )
        # refresh 需要 created_at 可读
        created_task.created_at = datetime.now(timezone.utc)

        def fake_add(obj):
            obj.id = obj.id or uuid.uuid4()
            obj.created_at = datetime.now(timezone.utc)
        fake_db.add.side_effect = fake_add
        fake_db.commit.return_value = None
        fake_db.refresh.return_value = None

        # mock Celery
        from unittest.mock import patch
        with patch.object(main_mod.process_video, "delay") as mock_delay:
            mock_delay.return_value.id = "celery-id-123"
            r = client.post(
                "/api/v1/tasks",
                json={
                    "url": "https://www.douyin.com/video/xxx",
                    "config": {"raw_text": "测试文案"},
                },
            )
            print(f"     POST 响应: {r.status_code} {r.text[:200]}")
            assert r.status_code == 201, r.text
            body = r.json()
            assert "task_id" in body
            assert body["status"] == TaskStatus.PENDING
            assert mock_delay.called, "process_video.delay 未被调用"
            print(f"[OK] 7. POST /api/v1/tasks -> 201, task_id={body['task_id'][:8]}..., Celery delay 已投递")
    finally:
        app.dependency_overrides.clear()

print()
print("=" * 60)
print("  步骤 5 自检 全部通过 ✅  端点 / CORS / 校验 / 404 / Celery 投递")
print("=" * 60)
