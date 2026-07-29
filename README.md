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

That brings up `postgres`, `redis`, a one-shot `migrate` service (applies Alembic migrations, then exits; `api` and `worker` both wait for it to finish before starting), `api`, `worker`, and `web`. See [Deployment](#deployment) below for a full explanation of that startup order, the env vars that matter for anything beyond local use, how to point a real Twilio number at a deployed instance, and what this setup doesn't cover.

To receive real Twilio webhooks locally, expose the `api` service with a tunnel (e.g. `ngrok http 8000`) and point your Twilio phone number's voice/SMS webhook URLs at the tunnel's `/v1/webhooks/voice/incoming` and `/v1/webhooks/sms/incoming`; see [Deployment](#deployment) for the details.

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

The `docker-compose.yml` in `infra/` is the primary deployment target for v1: a single-host stack suitable for a small/medium production workload. It runs `postgres`, `redis`, a one-shot `migrate` service, `api`, `worker`, and `web`. See [docs/ROADMAP.md](docs/ROADMAP.md) for the longer-term roadmap and "What this setup doesn't cover" below for what's explicitly out of scope today.

### 1. Configure environment

```bash
cp apps/api/.env.example apps/api/.env
```

`docker compose` reads variable overrides from a `.env` file in the directory it's run from (or the project directory passed via `--project-directory`), and from whatever is already in your shell environment, not from `apps/api/.env`, which is only consumed by the API process itself when running outside Docker. For a Docker deployment, export the variables below in your shell, or put them in a `.env` file next to `infra/docker-compose.yml`, before running `docker compose up`.

Every setting below has a working default so the stack boots with zero configuration, which is convenient for local development and exactly why you must not skip this step in a real deployment: every default is a publicly known placeholder value. The full list of settings with insecure `dev-*`/placeholder defaults lives in [`apps/api/app/core/config.py`](apps/api/app/core/config.py); the ones that matter for a production deployment are:

| Variable | Default | Why it matters |
|---|---|---|
| `JWT_SECRET` | `dev-secret-change-me-32-bytes-min` | Signs session JWTs for the dashboard. Anyone who knows this value (it's in the public repo) can forge a valid session for any user. Generate a real one: `python -c "import secrets; print(secrets.token_urlsafe(32))"`. |
| `API_KEY_PEPPER` | `dev-api-key-pepper-change-me` | HMAC pepper used to hash dashboard API keys and refresh tokens before storing them. Same exposure as `JWT_SECRET` if left at the default: a known pepper makes stored hashes reversible-by-guessing for anyone who also gets the database. Generate the same way as `JWT_SECRET`, as a separate value. |
| `TWILIO_ACCOUNT_SID` | `dev-placeholder-account-sid` | Your Twilio account SID. Left at the default, outbound SMS/voice calls to the Twilio REST API fail; the app still boots (needed for local dev and tests without a Twilio account) but can't act on your behalf. |
| `TWILIO_AUTH_TOKEN` | `dev-placeholder-auth-token` | Your Twilio auth token, used to verify the `X-Twilio-Signature` header on every inbound webhook (see `app/api/webhooks.py`). Left at the default, real requests from Twilio fail signature verification and are rejected with a 403, so the agent never receives them. |
| `TWILIO_PHONE_NUMBER` | `+15005550006` (Twilio's documented "magic" test number) | The number EchoFlow answers calls and texts on, used to resolve which organization an inbound webhook belongs to. Left at the default, no real caller can ever reach it; there's no number pointed at your deployment. |
| `GEMINI_API_KEY` | `dev-placeholder-gemini-key` | Your Gemini API key, used for the agent's function-calling loop (order lookup, knowledge search, appointment booking). Left at the default, every conversation turn fails once it reaches the agent step, so calls and texts get answered but the agent can't actually do anything. |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | `echoflow` / `echoflow` / `echoflow` | Postgres superuser credentials for the `postgres` container. If you override `POSTGRES_PASSWORD`, also update `DATABASE_URL` (below) to match: compose cannot substitute one variable's value inside another's default, so the two have to be kept in sync by hand. |
| `DATABASE_URL` | `postgresql+asyncpg://echoflow:echoflow@postgres:5432/echoflow` | Full connection string used by `migrate`, `api`, and `worker`. Only needs overriding if you changed the Postgres credentials above. |
| `CORS_ALLOWED_ORIGINS` | `http://localhost:3000` | Comma-separated list of origins the browser is allowed to call the API from. Defaults to the web app's local dev origin; if `web` is served from anywhere else, requests from the browser are blocked until this is updated to match. |
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000` | The URL the browser (not the Docker network) uses to reach the API. Baked into the `web` build at image build time, not read at container runtime (it's a Next.js `NEXT_PUBLIC_*` variable), so changing it requires `docker compose up --build`, not just a restart. Keep it and `CORS_ALLOWED_ORIGINS` pointed at each other: this is where the web app is served from, that's who the API needs to allow. |

None of these are prompted for or validated at startup; every one silently keeps its insecure default if you don't set it. There's no fail-fast check today, so this list is the closest thing to one, read it before deploying anywhere reachable outside your own machine.

### 2. Bring up the stack

```bash
docker compose -f infra/docker-compose.yml up --build -d
```

Startup ordering is enforced by `depends_on` conditions, not just declaration order: `postgres` and `redis` each have a healthcheck, and `migrate` waits for `postgres` to report healthy before it runs `alembic upgrade head` and exits. `api` and `worker` both wait on `migrate` reaching a successful exit (`condition: service_completed_successfully`) as well as `postgres`/`redis` being healthy, so neither one can start serving requests or pulling jobs against a database that hasn't been migrated yet. `web` waits on `api` being healthy (not just started) before it starts.

Check everything is healthy:

```bash
docker compose -f infra/docker-compose.yml ps
curl http://localhost:8000/health
```

`docker compose ps` should show `migrate` as `Exited (0)` and every other service as `running (healthy)`.

### Pointing a Twilio number at a deployed instance

Twilio needs a publicly reachable HTTPS URL to deliver webhooks to; it cannot reach a plain `localhost:8000` or an internal, unrouted host address. For a real deployment, put a reverse proxy with a valid TLS certificate in front of `api` (see "What this setup doesn't cover" below); for local testing, expose the `api` service with a tunnel instead:

```bash
ngrok http 8000
```

Then, in the Twilio console, open the phone number you want EchoFlow to answer on and set:

- **Voice: "A call comes in"**: webhook, `POST`, `https://<your-domain-or-tunnel>/v1/webhooks/voice/incoming`
- **Messaging: "A message comes in"**: webhook, `POST`, `https://<your-domain-or-tunnel>/v1/webhooks/sms/incoming`

Both endpoints verify the `X-Twilio-Signature` header against `TWILIO_AUTH_TOKEN` (see the table above), so requests that don't genuinely originate from Twilio, or arrive after `TWILIO_AUTH_TOKEN` was left at its placeholder, are rejected with a 403 rather than reaching the agent. The number itself also has to already exist as an `Organization`'s configured number in the database for a webhook to resolve to a conversation; an unrecognized `To` number gets a 404, not an agent reply.

### Viewing logs

```bash
docker compose -f infra/docker-compose.yml logs -f            # all services
docker compose -f infra/docker-compose.yml logs -f api worker # just the agent-facing services
```

Each container currently logs plain text to stdout, which `docker compose logs` captures as-is; if structured JSON logging (see [docs/TDD.md](docs/TDD.md) section 8) has landed by the time you're reading this, the same commands still work, each line is just a JSON object instead of plain text, and is easy to pipe into `jq` or a log aggregator.

### Persistent data

`postgres_data` is a named Docker volume (declared at the bottom of `docker-compose.yml`), so contacts, conversations, messages, orders, and knowledge documents all survive `docker compose down` and container restarts. It's only removed by an explicit `docker compose down -v`.

### Hardening notes

- `postgres` and `redis` publish their ports bound to `127.0.0.1` only (`127.0.0.1:5432:5432`, `127.0.0.1:6379:6379`), not `0.0.0.0`, so they're reachable for local debugging (`psql`, `redis-cli`) but not from off the host. `api` and `web` publish on all interfaces since they're meant to be reachable; put a reverse proxy in front of them (see below) rather than further restricting these.
- `postgres`, `redis`, `api`, `worker`, and `web` all run with `restart: unless-stopped`, so the stack comes back up on its own after a host reboot or a container crash. `migrate` intentionally has no restart policy: it's meant to run once and exit 0, not loop.
- Redis has no authentication enabled in this setup; it relies entirely on not being reachable off-host (the point of the `127.0.0.1` port binding above). If you ever split this across multiple hosts, you need to add real auth/TLS before doing so.

### What this setup doesn't cover

- **TLS termination**: put a reverse proxy (nginx, Caddy, a cloud load balancer) in front of `api` and `web` for anything beyond local testing. Nothing in this compose file terminates TLS, and Twilio requires HTTPS webhook URLs, so this is not optional for a real deployment.
- **Horizontal scaling**: `worker` can be scaled with `docker compose up --scale worker=3`, since RQ workers just compete for jobs on the same queue. `api` and `web` are not designed to be scaled behind a load balancer by this compose file; that requires the reverse proxy above and is out of scope for now.
- **Managed/HA Postgres and Redis**: the compose file runs single-instance containers with no replication or failover. Fine for the workload this is designed for. If you need HA, point `DATABASE_URL`/`REDIS_URL` at managed instances instead of the bundled containers.

## License

MIT, see [LICENSE](LICENSE).
