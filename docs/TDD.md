# Technical Design Document: EchoFlow

## Status
Draft v1.0 — Foundation phase. Companion to [PRD.md](PRD.md) and [ARCHITECTURE.md](ARCHITECTURE.md).

## 1. Overview
EchoFlow is a multi-package repository with three deployable units, an API service, a web dashboard, and a background worker, sharing one Postgres database and one Redis instance. No SDK package (same shape as Nexus); Twilio talks to the API directly via webhooks, and staff use the API via the dashboard.

## 2. Repository layout
```
echoflow/
  apps/
    api/                FastAPI backend (webhooks, agent orchestration, dashboard API, worker entrypoint)
      app/
        api/             route modules (webhooks, dashboard, auth)
        models/          SQLAlchemy models
        schemas/         Pydantic schemas
        services/        business logic (twilio, knowledge base, appointments, orders, notifications)
        agent/           LangGraph agent graph + tool definitions
        core/            config, security, db session
        workers/         RQ job definitions
      alembic/           migrations
      tests/
    web/                 Next.js dashboard
      app/               App Router routes
      components/
      lib/
      tests/
  infra/
    docker-compose.yml
  docs/
  .github/workflows/
```

## 3. Backend design

### 3.1 Tech stack
Python 3.12, FastAPI, SQLAlchemy 2.0 (async engine, asyncpg driver), Alembic, Pydantic v2, RQ + Redis for background jobs, `argon2-cffi` for password hashing, `PyJWT` for JWT, `langgraph` for agent orchestration, `google-genai` for Gemini function calling, `twilio` (official Python SDK) for TwiML generation, outbound REST calls, and webhook signature verification.

### 3.2 Services layered under `app/services/`
- `twilio_service`: verifies `X-Twilio-Signature` on inbound webhooks (`twilio.request_validator.RequestValidator`), builds TwiML responses (`<Gather>`, `<Say>`, `<Message>`, `<Hangup>`), and sends outbound SMS via the Twilio REST client.
- `conversation_service`: finds-or-creates `Contact`/`Conversation` rows for an inbound webhook, persists turns as `Message` rows, loads recent history for agent context.
- `knowledge_service`: CRUD for `KnowledgeDocument`, Postgres full-text search (`content_tsv`, GIN index, `ts_rank`-scored) backing the agent's `knowledge_search` tool.
- `appointment_service`: business-hours-aware availability calculation, appointment creation/cancellation, backing the agent's `check_availability` and `book_appointment` tools.
- `order_service`: CRUD and phone/order-number lookup for `Order` rows, backing the agent's `lookup_order` tool.
- `notification_service`: enqueues `notify_appointment` jobs (confirmation on booking, reminder ahead of `scheduled_at`) and sends them from the worker via `twilio_service`.
- `auth_service`: API key issuance/verification, JWT session issuance/verification (same design as Helios and Nexus).

### 3.3 Agent orchestration (`app/agent/`)
A LangGraph `StateGraph` implementing a tool-calling loop: an `agent` node calls Gemini with function-calling declarations for the four tools plus the conversation history; if Gemini's response includes tool calls, a `tools` node executes them against the relevant service and appends the results to state, looping back to `agent`; once Gemini returns a plain text response, the graph ends. State carries the organization/contact/conversation ids (so tools can scope their queries), the running message history, and the pending/executed tool calls. Conversation memory is loaded from the `messages` table before the graph runs (windowed to the most recent N turns for v1), the same approach Nexus uses, rather than relying on LangGraph's own checkpointer.

Tools:
- `knowledge_search(query: str)` — calls `knowledge_service`, returns top-matching document excerpts.
- `lookup_order(order_number: str | None, phone_number: str | None)` — calls `order_service`.
- `check_availability(date: str)` — calls `appointment_service`, returns open slots for that date.
- `book_appointment(scheduled_at: str, duration_minutes: int, notes: str | None)` — calls `appointment_service`, creates the appointment, triggers a confirmation notification.

### 3.4 API surface (high level, detailed OpenAPI generated at `/docs`)
- `POST /v1/webhooks/voice/incoming`, `POST /v1/webhooks/voice/respond` — Twilio voice webhooks, signature-verified, return TwiML.
- `POST /v1/webhooks/sms/incoming` — Twilio SMS webhook, signature-verified, returns TwiML.
- `GET /v1/conversations`, `GET /v1/conversations/{id}` — transcript history, JWT session authenticated.
- `POST /v1/knowledge-documents`, `GET /v1/knowledge-documents`, `DELETE /v1/knowledge-documents/{id}` — knowledge base management.
- `GET /v1/appointments`, `POST /v1/appointments`, `PATCH /v1/appointments/{id}` — appointment management (staff-initiated, in addition to agent-initiated).
- `POST /v1/business-hours` — configure weekly availability.
- `POST /v1/orders`, `GET /v1/orders` — order records (seeded by staff, standing in for a real order-system connector).
- `GET /v1/analytics/overview` — call/SMS volume, appointments booked, average conversation length.
- `POST /v1/auth/register`, `POST /v1/auth/login`, `POST /v1/auth/refresh` — JWT session auth for dashboard users.
- `POST /v1/api-keys`, `DELETE /v1/api-keys/{id}` — issue/revoke an API key, JWT session authenticated.
- `GET /health` — unauthenticated liveness check.

### 3.5 Async processing
Booking an appointment (whether agent-initiated mid-conversation or staff-initiated via the dashboard API) enqueues a `notify_appointment(appointment_id, kind="confirmation")` job onto Redis so the webhook/API response is not blocked on an outbound Twilio REST call. The worker also runs a periodic sweep (an RQ scheduled job, checked on worker startup and every few minutes) that enqueues `notify_appointment(appointment_id, kind="reminder")` for appointments starting within the reminder lead time that have not yet had a reminder sent.

### 3.6 Auth and webhook security design
Two different trust boundaries, verified two different ways:
- **Twilio webhooks**: no API key or JWT. Twilio signs every request with `X-Twilio-Signature`, an HMAC-SHA1 of the full callback URL plus sorted POST parameters, keyed by `TWILIO_AUTH_TOKEN`. `twilio_service.verify_signature` recomputes it and rejects the request (`403`) on mismatch. This is pure shared-secret cryptography, fully testable without a live Twilio account: tests compute a valid signature with a test auth token the same way Twilio does, and separately assert a tampered signature is rejected.
- **Dashboard/admin API**: reuses Helios/Nexus's two-scheme design. `require_api_key` reads `Authorization: Bearer efl_live_...`, looked up by a server-peppered HMAC-SHA256 hash, resolves an `Organization`. `require_session` reads a JWT, resolves a `User` scoped to an `Organization`. Passwords hashed with Argon2; API keys and refresh tokens hashed with the peppered HMAC (same rationale as Nexus: they're looked up by hash equality with no accompanying identifier to narrow the search, so a deterministic hash is required). JWTs are 15-minute access tokens with 7-day single-use rotating refresh tokens.

Every `Contact`, `Conversation`, `KnowledgeDocument`, `Order`, `Appointment`, and `PhoneNumber` row is scoped to an `organization_id`; webhook handlers resolve the organization from the Twilio `To` number before touching any of these tables, so one business's data is never reachable through another's phone number.

## 4. Frontend design
Next.js 14 App Router, TypeScript, Tailwind, shadcn/ui components, TanStack Query for server state. Conversation log view polls/refetches transcripts; appointment calendar and knowledge base management are standard CRUD views against the dashboard API.

## 5. Data model
See [ARCHITECTURE.md](ARCHITECTURE.md) for the entity relationship diagram. Core tables: `organizations`, `users`, `api_keys`, `phone_numbers`, `contacts`, `conversations`, `messages`, `knowledge_documents`, `orders`, `business_hours`, `appointments`, `notifications`.

## 6. Testing strategy
- **Unit tests**: services (`knowledge_service` FTS ranking, `appointment_service` availability calculation, `twilio_service` signature verification and TwiML generation) tested in isolation with fixture inputs.
- **Integration tests**: API routes tested against a real (test) Postgres via `httpx.AsyncClient`, using pytest fixtures for a transactional session per test. Webhook tests construct real, validly-signed Twilio requests (and separately, invalidly-signed ones) using a test `TWILIO_AUTH_TOKEN`, no live Twilio account needed.
- **Agent graph tests**: the LangGraph agent tested with Gemini calls mocked (deterministic tool-call sequences), asserting the graph calls the right tool with the right arguments and terminates correctly; a documented manual smoke test against the real Gemini API and a real Twilio trial number is run once credentials are available, per [ROADMAP.md](ROADMAP.md).
- **Frontend unit tests**: component tests via Vitest + Testing Library.
- **API contract tests**: OpenAPI schema validated against example requests/responses in CI.
- Coverage tracked via `pytest-cov`, reported in CI job summary.

## 7. CI/CD
GitHub Actions, one workflow (`ci.yml`) with parallel jobs: `api-test` (ruff, mypy, pytest against a real Postgres service container plus a real Redis service container), `web-test` (eslint, tsc, vitest, next build), `docker-build` (build all Dockerfiles to verify they build cleanly). All required to pass before merge.

## 8. App-level observability
Same dogfooded structured-logging design as Helios and Nexus, reused for consistency across the lab.

- **JSON logs everywhere**: `app/core/logging.py#configure_logging` replaces the root logger's handler with one JSON line per record (`timestamp`, `level`, `logger`, `message`, plus `correlation_id` when set), called at startup by both the API process and the RQ worker.
- **Correlation IDs**: a `contextvars.ContextVar` holds the current request or job's id, set via `logging.setLogRecordFactory`. `CorrelationIdMiddleware` sets it per API request (webhook requests get one too, tagged with the Twilio `CallSid`/`MessageSid` where available so a call/text's full log trail is traceable); `notify_appointment` sets a `job-<hex>` id per job.

## 9. Deployment
Docker Compose is the primary deployment target for v1: `docker-compose.yml` defines `api`, `worker`, `web`, `postgres`, `redis`. Twilio webhooks require a publicly reachable URL for the `api` service; the deployment guide (root README, once written) covers pointing a Twilio phone number's webhook configuration at a deployed instance. Environment configuration via `.env` (see `.env.example`); `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_PHONE_NUMBER`, and `GEMINI_API_KEY` are the required external secrets.

## 10. Tradeoffs and future improvements
- Twilio-native speech-to-text/text-to-speech (`<Gather input="speech">`, `<Say>`) over a custom Media Streams + third-party STT/TTS pipeline: dramatically simpler and fully testable without live audio, at the cost of Twilio's ASR/TTS quality/latency ceiling rather than a best-in-class dedicated provider. A documented future upgrade path if voice quality needs improve.
- A single tool-calling LangGraph loop over per-intent hardcoded branching: lets the agent compose multiple tools in one turn (e.g. look up an order, then offer to book a follow-up appointment) and is the more realistic shape of a production conversational agent.
- `orders` as an EchoFlow-owned table rather than a real e-commerce integration: keeps the build fully self-contained and testable; the `order_service` interface is the seam a real connector would replace.
- Windowed conversation memory (last N turns) over summarization for v1: simpler, sufficient for typical short call/SMS interactions; matches the precedent set in Nexus.
- Reusing Helios/Nexus's exact dashboard auth pattern rather than designing a new one: consistency across the lab's repos, and it's already a proven design. Twilio webhook auth is necessarily a separate, signature-based scheme since Twilio cannot be handed an API key or JWT.
