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
