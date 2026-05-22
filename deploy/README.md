# Production Deploy

This app is deployed as:

- `web`: Caddy serving the Vite frontend, reverse-proxying `/api` to FastAPI, and issuing HTTPS certificates automatically.
- `backend`: FastAPI on port 8000.
- `worker`: Celery worker using the same backend image.
- `backend_output`: shared volume for generated covers and intermediate outputs, mounted by both `backend` and `worker`.
- `video-plan-jp-proxy-tunnel.service`: legacy helper for the old proxy relay path. The current production AI path is official direct API access and does not depend on this unit.

## Server Commands

```bash
git clone <repo> /opt/video-plan
cd /opt/video-plan
cp .env.production.example .env
docker compose up -d --build
```

## Official Direct AI APIs

OpenAI, Gemini, and DeepSeek calls should use official endpoints directly. Keep `OFFICIAL_API_DIRECT_ONLY=True` and leave provider proxy variables empty unless you explicitly disable direct-only mode for a separate maintenance test.

Required endpoint defaults:

- `OPENAI_BASE_URL=https://api.openai.com/v1`
- `IMAGE_BASE_URL=https://api.openai.com/v1`
- `DEEPSEEK_BASE_URL=https://api.deepseek.com/v1`
- `OPENAI_HTTPS_PROXY=`
- `GOOGLE_HTTPS_PROXY=`

Health checks from the Beijing ECS:

```bash
docker compose exec worker python - <<'PY'
import httpx
from app.core.config import settings
with httpx.Client(timeout=30) as client:
    print(client.get("https://api.openai.com/v1/models", headers={"Authorization": "Bearer " + settings.OPENAI_API_KEY}).status_code)
PY
```

DNS:

- `A @ -> <server_public_ip>`
- `A www -> <server_public_ip>`

Open firewall/security-group ports `80` and `443`.

For cover generation, set:

- `OFFICIAL_API_DIRECT_ONLY=True`
- `COVER_BATCH_V2_ENABLED=True`
- `OPENAI_API_KEY=<your official OpenAI key>`
- `IMAGE_BASE_URL=https://api.openai.com/v1`
- `IMAGE_MODEL=gpt-image-2`
- `COVER_BATCH_MAX_CONCURRENT_ITEMS=2` initially, then raise only after checking 429/timeout metrics.

New batch cover uploads are stored in the database-backed V2 queue. Old file-backed cover jobs remain readable for compatibility.
