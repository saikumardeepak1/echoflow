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
