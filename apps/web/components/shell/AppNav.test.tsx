import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AppNav } from "./AppNav";

const replace = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace }),
  usePathname: () => "/conversations",
}));

const logout = vi.fn();
vi.mock("@/lib/api-client", () => ({
  logout: () => logout(),
}));

describe("AppNav", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders links to all four dashboard sections", () => {
    render(<AppNav />);

    expect(screen.getByRole("link", { name: "Conversations" })).toHaveAttribute(
      "href",
      "/conversations",
    );
    expect(screen.getByRole("link", { name: "Appointments" })).toHaveAttribute(
      "href",
      "/appointments",
    );
    expect(screen.getByRole("link", { name: "Knowledge Base" })).toHaveAttribute(
      "href",
      "/knowledge-base",
    );
    expect(screen.getByRole("link", { name: "Analytics" })).toHaveAttribute("href", "/analytics");
  });

  it("marks the active section with aria-current", () => {
    render(<AppNav />);

    expect(screen.getByRole("link", { name: "Conversations" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(screen.getByRole("link", { name: "Appointments" })).not.toHaveAttribute("aria-current");
  });

  it("logs out and redirects to /login when the log out button is clicked", () => {
    render(<AppNav />);

    fireEvent.click(screen.getByRole("button", { name: /log out/i }));

    expect(logout).toHaveBeenCalledOnce();
    expect(replace).toHaveBeenCalledWith("/login");
  });
});
