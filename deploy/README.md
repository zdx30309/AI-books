# Production Deploy

This app is deployed as:

- `web`: Caddy serving the Vite frontend, reverse-proxying `/api` to FastAPI, and issuing HTTPS certificates automatically.
- `backend`: FastAPI on port 8000.
- `worker`: Celery worker using the same backend image.
- `backend_output`: shared volume for generated covers and intermediate outputs, mounted by both `backend` and `worker`.
- `video-plan-jp-proxy-tunnel.service`: SSH tunnel to the Japan proxy relay. Beijing ECS needs this relay for OpenAI and Gemini egress.

## Server Commands

```bash
git clone <repo> /opt/video-plan
cd /opt/video-plan
cp .env.production.example .env
docker compose up -d --build
```

## Official AI APIs Through Japan Proxy

OpenAI and Gemini calls use official provider endpoints, but Beijing ECS must egress through the local Privoxy listener backed by the Japan proxy. Keep the official base URLs and set the provider proxy variables to `http://172.18.0.1:7898`.

Required endpoint defaults:

- `OPENAI_BASE_URL=https://api.openai.com/v1`
- `IMAGE_BASE_URL=https://api.openai.com/v1`
- `DEEPSEEK_BASE_URL=https://api.deepseek.com/v1`
- `OFFICIAL_API_DIRECT_ONLY=False`
- `OPENAI_HTTPS_PROXY=http://172.18.0.1:7898`
- `GOOGLE_HTTPS_PROXY=http://172.18.0.1:7898`

Health checks from the Beijing ECS:

```bash
docker compose exec worker python - <<'PY'
import httpx
from app.core.config import settings
proxy = settings.OPENAI_HTTPS_PROXY or None
with httpx.Client(timeout=30, proxy=proxy) as client:
    print(client.get("https://api.openai.com/v1/models", headers={"Authorization": "Bearer " + settings.OPENAI_API_KEY}).status_code)
PY
```

DNS:

- `A @ -> <server_public_ip>`
- `A www -> <server_public_ip>`

Open firewall/security-group ports `80` and `443`.

For cover generation, set:

- `OFFICIAL_API_DIRECT_ONLY=False`
- `COVER_BATCH_V2_ENABLED=True`
- `OPENAI_API_KEY=<your official OpenAI key>`
- `IMAGE_BASE_URL=https://api.openai.com/v1`
- `IMAGE_MODEL=gpt-image-2`
- `OPENAI_HTTPS_PROXY=http://172.18.0.1:7898`
- `COVER_BATCH_MAX_CONCURRENT_ITEMS=2` initially, then raise only after checking 429/timeout metrics.

New batch cover uploads are stored in the database-backed V2 queue. Old file-backed cover jobs remain readable for compatibility.
