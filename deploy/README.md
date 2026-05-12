# Production Deploy

This app is deployed as:

- `web`: Caddy serving the Vite frontend, reverse-proxying `/api` to FastAPI, and issuing HTTPS certificates automatically.
- `backend`: FastAPI on port 8000.
- `worker`: Celery worker using the same backend image.
- `backend_output`: shared volume for generated covers and intermediate outputs, mounted by both `backend` and `worker`.
- `video-plan-jp-proxy-tunnel.service`: Beijing ECS local tunnel to the Japan HTTP proxy for OpenAI/Gemini calls. This is a server-side production dependency and must not rely on a developer laptop proxy.

## Server Commands

```bash
git clone <repo> /opt/video-plan
cd /opt/video-plan
cp .env.production.example .env
docker compose up -d --build
```

## OpenAI/Gemini Proxy

OpenAI and Gemini traffic should leave through the Japan proxy server. On the Beijing ECS, install the systemd unit in `deploy/video-plan-jp-proxy-tunnel.service` and keep `.env` pointed at `172.18.0.1:7896`:

```bash
cp deploy/video-plan-jp-proxy-tunnel.service /etc/systemd/system/video-plan-jp-proxy-tunnel.service
systemctl daemon-reload
systemctl enable --now video-plan-jp-proxy-tunnel.service
systemctl status video-plan-jp-proxy-tunnel.service
```

Health checks from the Beijing ECS:

```bash
docker compose exec worker python - <<'PY'
import httpx
from app.core.config import settings
with httpx.Client(proxy=settings.OPENAI_HTTPS_PROXY, timeout=30) as client:
    print(client.get("https://api.openai.com/v1/models", headers={"Authorization": "Bearer " + settings.OPENAI_API_KEY}).status_code)
PY
```

DNS:

- `A @ -> <server_public_ip>`
- `A www -> <server_public_ip>`

Open firewall/security-group ports `80` and `443`.

For cover generation, set:

- `IMAGE_BASE_URL=https://api.tu-zi.com/v1`
- `IMAGE_API_KEY=<your image provider key>`
- `IMAGE_MODEL=gpt-image-2`
- Leave `IMAGE_QUALITY`, `IMAGE_RESPONSE_FORMAT`, and `IMAGE_OUTPUT_FORMAT` empty for the tu-zi routing chain unless that provider enables those parameters for your group.
