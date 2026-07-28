import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, login, logout } from "./api-client";
import { clearTokens, getAccessToken, getRefreshToken, getStoredUser } from "./token-storage";

const TOKEN_PAIR = {
  access_token: "access-1",
  refresh_token: "refresh-1",
  token_type: "bearer",
  expires_in: 900,
  user: { id: "1", organization_id: "org-1", email: "a@b.com", role: "admin" },
};

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("login", () => {
  beforeEach(() => {
    clearTokens();
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("posts credentials to /v1/auth/login and stores the returned token pair", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(TOKEN_PAIR));

    const result = await login({ email: "a@b.com", password: "password123" });

    expect(fetch).toHaveBeenCalledTimes(1);
    const [url, init] = vi.mocked(fetch).mock.calls[0];
    expect(url).toContain("/v1/auth/login");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(init?.body as string)).toEqual({
      email: "a@b.com",
      password: "password123",
    });

    expect(result).toEqual(TOKEN_PAIR);
    expect(getAccessToken()).toBe("access-1");
    expect(getRefreshToken()).toBe("refresh-1");
    expect(getStoredUser()).toEqual(TOKEN_PAIR.user);
  });

  it("throws an ApiError with the server-provided detail message on failure", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      jsonResponse({ detail: "Invalid email or password" }, 401),
    );

    await expect(login({ email: "a@b.com", password: "wrong" })).rejects.toMatchObject({
      name: "ApiError",
      message: "Invalid email or password",
      status: 401,
    });

    expect(getAccessToken()).toBeNull();
  });

  it("does not attach an Authorization header for the login request itself", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(jsonResponse(TOKEN_PAIR));

    await login({ email: "a@b.com", password: "password123" });

    const [, init] = vi.mocked(fetch).mock.calls[0];
    const headers = new Headers(init?.headers);
    expect(headers.has("Authorization")).toBe(false);
  });
});

describe("logout", () => {
  beforeEach(() => {
    clearTokens();
  });

  it("clears any stored session", () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(jsonResponse(TOKEN_PAIR)));

    return login({ email: "a@b.com", password: "password123" }).then(() => {
      expect(getAccessToken()).not.toBeNull();
      logout();
      expect(getAccessToken()).toBeNull();
      vi.unstubAllGlobals();
    });
  });
});

describe("ApiError", () => {
  it("carries the HTTP status alongside the message", () => {
    const error = new ApiError("boom", 500);
    expect(error.message).toBe("boom");
    expect(error.status).toBe(500);
    expect(error.name).toBe("ApiError");
  });
});
