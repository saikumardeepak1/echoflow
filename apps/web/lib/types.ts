/**
 * Request/response shapes mirrored from the documented auth contract
 * (docs/TDD.md section 3.4, `POST /v1/auth/login`). The auth API itself
 * (issue #1 / #3) is being built separately; keep these in sync with
 * apps/api/app/schemas/auth.py once that lands.
 */

export interface LoginRequest {
  email: string;
  password: string;
}

export interface RefreshRequest {
  refresh_token: string;
}

export interface UserResponse {
  id: string;
  organization_id: string;
  email: string;
  role: string;
}

export interface TokenPairResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
  user: UserResponse;
}

/**
 * Mirrors apps/api/app/schemas/appointment.py (AppointmentResponse /
 * AppointmentUpdateRequest, issue #12). Keep in sync if the API contract
 * changes.
 */
export type AppointmentStatus = "scheduled" | "completed" | "cancelled";

export interface AppointmentResponse {
  id: string;
  contact_id: string;
  scheduled_at: string;
  duration_minutes: number;
  status: AppointmentStatus;
  notes: string | null;
  created_at: string;
}

export interface AppointmentUpdateRequest {
  scheduled_at?: string;
  duration_minutes?: number;
  notes?: string;
  status?: AppointmentStatus;
}

/**
 * Mirrors apps/api/app/schemas/knowledge_document.py. `content_tsv` (the
 * Postgres full-text search index) is intentionally omitted from the API
 * response and therefore from this type.
 */
export interface KnowledgeDocument {
  id: string;
  title: string;
  content: string;
  created_at: string;
}

export interface KnowledgeDocumentCreateRequest {
  title: string;
  content: string;
}

/**
 * Mirrors apps/api/app/schemas/conversation.py. `channel` and `status` are
 * kept as `string` (rather than a union) since the API itself treats them
 * as plain strings; `ConversationChannel` below is the narrower set the
 * dashboard's filter UI actually offers.
 */
export type ConversationChannel = "voice" | "sms";

export interface Message {
  id: string;
  role: string;
  content: string;
  created_at: string;
}

export interface Conversation {
  id: string;
  organization_id: string;
  contact_id: string;
  channel: string;
  status: string;
  contact_phone_number: string;
  created_at: string;
}

export interface ConversationDetail extends Conversation {
  messages: Message[];
}
