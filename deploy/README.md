# Production Deploy

This app is deployed as:

- `web`: Caddy serving the Vite frontend, reverse-proxying `/api` to FastAPI, and issuing HTTPS certificates automatically.
- `backend`: FastAPI on port 8000.
- `worker`: Celery worker using the same backend image.
- `backend_output`: shared volume for generated covers and intermediate outputs, mounted by both `backend` and `worker`.

## Server Commands

```bash
git clone <repo> /opt/video-plan
cd /opt/video-plan
cp .env.production.example .env
docker compose up -d --build
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
