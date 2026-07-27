# EchoFlow

Voice and SMS agent platform: an AI agent answers inbound calls and texts, holds a real conversation, looks up orders, answers questions from your knowledge base, and books appointments.

## Why EchoFlow

Small and mid-size businesses lose calls and customer trust because they cannot staff a phone line around the clock. EchoFlow connects to a Twilio number and gives every caller (voice or SMS) a conversational agent that can actually do things: search your FAQ, look up an order, check appointment availability, and book a slot, end to end, without a human picking up.

## Documentation

- [Product Requirements Document](docs/PRD.md)
- [Technical Design Document](docs/TDD.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Roadmap](docs/ROADMAP.md)
- **API reference**: interactive, generated from the running API. Swagger UI at `/docs`, ReDoc at `/redoc` (e.g. http://localhost:8000/docs once the stack is up).

## Status

Early development. See [Roadmap](docs/ROADMAP.md) and the [issue tracker](https://github.com/saikumardeepak1/echoflow/issues) for current progress.

## Project structure

```
apps/api/    FastAPI backend: Twilio webhooks, agent orchestration, dashboard API
apps/web/    Next.js dashboard
infra/       Docker Compose and deployment config
docs/        Planning and architecture docs
```

## Getting started

Requires Docker, Python 3.12+, Node 20+, and a Twilio account (free trial works) plus a Gemini API key.

```bash
cp apps/api/.env.example apps/api/.env
# edit apps/api/.env: set a real JWT_SECRET, your TWILIO_* credentials, and your GEMINI_API_KEY

docker compose -f infra/docker-compose.yml up --build

# api: http://localhost:8000 (docs at /docs)
# web: http://localhost:3000
```

To receive real Twilio webhooks locally, expose the `api` service with a tunnel (e.g. `ngrok http 8000`) and point your Twilio phone number's voice/SMS webhook URLs at the tunnel's `/v1/webhooks/voice/incoming` and `/v1/webhooks/sms/incoming`.

### Running services individually

```bash
# API
cd apps/api
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --reload

# Worker (needs Postgres + Redis reachable, and migrations already applied)
cd apps/api && source .venv/bin/activate
python -m app.workers.worker

# Web
cd apps/web
cp .env.example .env.local
npm install
npm run dev
```

### Running tests

```bash
# API
cd apps/api && source .venv/bin/activate && pytest -q --cov=app

# Web
cd apps/web && npm run test
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the branch/PR workflow.

## Deployment

The `docker-compose.yml` in `infra/` is the primary deployment target for v1. See [docs/ROADMAP.md](docs/ROADMAP.md) for the longer-term roadmap. A full production deployment guide (env vars, hardening notes) will be added once the scaffolding and core services land, following the same pattern documented in the Helios and Nexus repos.

## License

MIT, see [LICENSE](LICENSE).
