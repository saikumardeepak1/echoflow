# Product Requirements Document: EchoFlow

## Status
Draft v1.0 — Foundation phase

## Summary
EchoFlow is a voice and SMS agent platform for small and mid-size businesses: an AI agent answers inbound calls and text messages, holds a real conversation, looks up orders, answers questions from a business's own knowledge base, and books appointments, all without a human picking up the phone. Businesses connect a Twilio number, upload a few FAQ documents, and get a receptionist that never misses a call.

## Problem statement
Small and mid-size businesses lose revenue and customer trust because they cannot staff a phone line around the clock:
- **Missed calls are missed revenue** — a call that goes to voicemail is usually a lost booking or a lost sale, not a returned call.
- **Repetitive questions consume staff time** — "what are your hours," "where's my order," "do you have an opening Tuesday" are high-volume, low-complexity, and pull staff away from higher-value work.
- **SMS is under-served** — most small-business phone systems have no automated way to hold a text conversation, even though customers increasingly prefer texting over calling.
- **Appointment booking still routes through a human** — checking availability and confirming a slot over the phone is exactly the kind of structured, tool-driven task an agent can do end-to-end.

## Goals
1. Answer an inbound phone call, hold a natural back-and-forth conversation using speech recognition and speech synthesis, and resolve the caller's request without a human.
2. Hold the same kind of conversation over SMS, with a customer who prefers texting to calling.
3. Give the agent tools to actually do things: search the business's knowledge base, look up an order, check appointment availability, and book an appointment.
4. Remember context within a conversation (multi-turn) so a caller does not have to repeat themselves.
5. Give the business a dashboard to see call/SMS transcripts, manage appointments, manage the knowledge base, and see usage analytics.
6. Be self-hostable via Docker Compose, with Twilio (voice + SMS) and a Gemini API key as the only required external dependencies.

## Non-goals (v1)
- Outbound cold-calling or marketing campaigns — EchoFlow only handles inbound conversations plus appointment-related outbound notifications (confirmations, reminders) triggered by the conversation itself.
- Real payment collection over voice/SMS (e.g. reading a card number to the agent) — out of scope for a portfolio-scope build and a genuine PCI-compliance concern.
- Multi-language support — English only for v1.
- A real e-commerce/order backend integration — order lookup works against EchoFlow's own `orders` table, seeded via the admin API, standing in for what would be a connector to a business's real order system.
- Custom voice cloning or non-Twilio speech providers — speech-to-text and text-to-speech run through Twilio's built-in call primitives (`<Gather input="speech">`, `<Say>`) for v1.

## Target users
- **Small/mid-size business owners** (salons, clinics, repair shops, local services) who cannot staff a phone line full-time but lose business when calls go unanswered.
- **Front-desk/ops staff** who currently spend their day on repetitive phone/text triage and want the routine cases handled automatically, escalating only what actually needs a human.
- **Engineering leadership** evaluating a reference implementation of a tool-calling conversational agent operating over real telephony (not just a chat widget).

## Core features

### Voice channel
- Inbound call handling via Twilio: greet the caller, transcribe their speech, respond with synthesized speech, loop until the request is resolved or the caller hangs up.
- Multi-turn spoken conversation with context carried across turns.

### SMS channel
- Inbound SMS handling via Twilio: hold a text conversation with the same agent intelligence as voice.
- Outbound SMS for appointment confirmations and reminders.

### Agent intelligence
- Tool-calling agent (Gemini function calling via LangGraph) with four tools: knowledge base search, order lookup, appointment availability check, appointment booking.
- Conversation memory scoped per contact (phone number) per business, carried across turns within a conversation and referenceable across the contact's conversation history.

### Knowledge base
- Business staff upload FAQ/policy documents (title + text) via the dashboard or admin API.
- Agent searches the knowledge base as a tool call and grounds its answer in the matched content.

### Appointment scheduling
- Business staff configure business hours and appointment duration.
- Agent checks availability and books an appointment as tool calls during a live conversation.
- Confirmation sent immediately (SMS); reminder sent ahead of the appointment time.

### Dashboard
- Conversation log: browse call/SMS transcripts per contact.
- Appointment calendar: upcoming/past appointments, status.
- Knowledge base management: add/edit/remove documents.
- Analytics overview: call volume, SMS volume, appointments booked, average conversation length, over time.

## Success criteria (v1)
- A phone call to a configured Twilio number is answered by the agent, the caller's speech is transcribed, and a spoken response is generated, entirely without human intervention.
- An SMS to the same number is answered by the same agent intelligence, holding a coherent multi-turn conversation.
- A caller can book an appointment end-to-end in a single conversation (check availability, confirm a slot, receive an SMS confirmation) with no human involved.
- A caller asking a question covered by an uploaded knowledge base document gets an answer grounded in that document.
- Entire stack (`api`, `worker`, `web`, `postgres`, `redis`) starts with a single `docker compose up`, with `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_PHONE_NUMBER`, and `GEMINI_API_KEY` as the only required external secrets.

## Open questions
- Whether conversation memory should be windowed (last N turns) or summarized once a contact's history grows long — starting windowed for simplicity, matching the precedent set in Nexus.
- Whether appointment reminder lead time should be configurable per business or fixed for v1 — starting fixed (24 hours), revisit if real usage shows a need.
