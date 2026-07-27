# Roadmap: EchoFlow

Tracked as GitHub milestones and issues on this repository. This doc is the human-readable mirror.

## Milestone 1: Foundation
- Project scaffolding & CI/CD pipeline
- Database schema design (Postgres + Alembic)
- API authentication (API keys + JWT sessions)
- Frontend app shell & design system setup

## Milestone 2: Voice & SMS Channel Integration
- Twilio signature verification & TwiML helpers
- Inbound SMS webhook & conversation persistence
- Inbound voice webhook & conversation persistence
- Phone number provisioning (link a Twilio number to an organization)
- Conversation transcript API

## Milestone 3: Agent Intelligence
- Knowledge base service (documents + Postgres full-text search)
- Order lookup service
- Appointment availability & booking service
- LangGraph tool-calling agent graph (Gemini function calling)
- Wire agent graph into voice & SMS webhooks
- Redis/RQ worker: outbound appointment notifications (confirmation & reminder)

## Milestone 4: Dashboard & Hardening
- Dashboard: conversation log viewer
- Dashboard: appointment calendar
- Dashboard: knowledge base management UI
- Dashboard: analytics overview
- Analytics aggregation API
- Structured logging & app-level observability
- Test coverage: unit + integration + webhook signature tests
- Deployment guide & production Docker Compose hardening
- API documentation (OpenAPI)

## Sequencing
Milestones are built roughly in order, but issues within a milestone may interleave where dependencies allow (e.g. knowledge base, order lookup, and appointment services can be built in parallel once the schema lands, and all three must land before the agent graph that calls them as tools). Each issue ships as its own feature branch and PR, see `CONTRIBUTING.md` for the branch naming and PR conventions used in this repo.

`TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, and `TWILIO_PHONE_NUMBER` are used env-var-driven throughout (webhook signature verification, TwiML generation, outbound SMS); Deepak signs up for Twilio's free trial account himself once the integration is built; a real Gemini API key is provided locally, not committed. Webhook signature verification, TwiML generation, conversation persistence, knowledge/order/appointment services, and the agent graph (with Gemini mocked) are all built and fully tested without live credentials for either provider; a manual smoke test against real Twilio and Gemini accounts is run once both are available.

## Explicitly out of scope for v1
- Outbound cold-calling/marketing campaigns
- Real payment collection over voice/SMS
- Multi-language support
- A real e-commerce/order backend integration
- Custom voice cloning or non-Twilio speech providers
- Managed cloud hosting (Docker Compose self-host only)
