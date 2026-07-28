import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactElement } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ConversationsView } from "./ConversationsView";

vi.mock("@/lib/api-client", () => {
  class ApiError extends Error {
    status: number;
    constructor(message: string, status: number) {
      super(message);
      this.name = "ApiError";
      this.status = status;
    }
  }
  return {
    ApiError,
    listConversations: vi.fn(),
  };
});

import { ApiError, listConversations } from "@/lib/api-client";

function renderWithQueryClient(ui: ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

const CONVERSATIONS = [
  {
    id: "conv-1",
    organization_id: "org-1",
    contact_id: "contact-1",
    channel: "sms",
    status: "open",
    contact_phone_number: "+15551234567",
    created_at: "2026-07-23T09:15:00Z",
  },
  {
    id: "conv-2",
    organization_id: "org-1",
    contact_id: "contact-2",
    channel: "voice",
    status: "closed",
    contact_phone_number: "+15559876543",
    created_at: "2026-07-22T14:00:00Z",
  },
];

describe("ConversationsView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows a loading state while the request is in flight", () => {
    vi.mocked(listConversations).mockReturnValueOnce(new Promise(() => {}));

    renderWithQueryClient(<ConversationsView />);

    expect(screen.getByRole("status")).toHaveTextContent(/loading conversations/i);
  });

  it("renders each conversation's channel, contact, status, and timestamp", async () => {
    vi.mocked(listConversations).mockResolvedValueOnce(CONVERSATIONS);

    renderWithQueryClient(<ConversationsView />);

    expect(await screen.findByText("+15551234567")).toBeInTheDocument();
    expect(screen.getByText("+15559876543")).toBeInTheDocument();
    expect(screen.getByText("sms")).toBeInTheDocument();
    expect(screen.getByText("voice")).toBeInTheDocument();
    expect(screen.getByText("open")).toBeInTheDocument();
    expect(screen.getByText("closed")).toBeInTheDocument();

    const links = screen.getAllByRole("link");
    expect(links.find((link) => link.getAttribute("href") === "/conversations/conv-1")).toBeTruthy();
    expect(links.find((link) => link.getAttribute("href") === "/conversations/conv-2")).toBeTruthy();
  });

  it("refetches with the selected channel when a filter button is clicked", async () => {
    vi.mocked(listConversations).mockResolvedValue(CONVERSATIONS);

    renderWithQueryClient(<ConversationsView />);

    await waitFor(() => expect(listConversations).toHaveBeenCalledWith(undefined));

    fireEvent.click(screen.getByRole("button", { name: "Voice" }));

    await waitFor(() => expect(listConversations).toHaveBeenCalledWith({ channel: "voice" }));
    expect(screen.getByRole("button", { name: "Voice" })).toHaveAttribute("aria-pressed", "true");
  });

  it("shows an empty state when there are no conversations", async () => {
    vi.mocked(listConversations).mockResolvedValueOnce([]);

    renderWithQueryClient(<ConversationsView />);

    expect(await screen.findByText(/no conversations found/i)).toBeInTheDocument();
  });

  it("shows a visible error message when the request fails", async () => {
    vi.mocked(listConversations).mockRejectedValueOnce(new ApiError("Server error", 500));

    renderWithQueryClient(<ConversationsView />);

    expect(await screen.findByRole("alert")).toHaveTextContent("Server error");
  });
});
