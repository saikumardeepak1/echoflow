import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import type { ReactElement } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ConversationDetailView } from "./ConversationDetailView";

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
    getConversation: vi.fn(),
  };
});

import { ApiError, getConversation } from "@/lib/api-client";

function renderWithQueryClient(ui: ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

const CONVERSATION_DETAIL = {
  id: "conv-1",
  organization_id: "org-1",
  contact_id: "contact-1",
  channel: "sms",
  status: "open",
  contact_phone_number: "+15551234567",
  created_at: "2026-07-23T09:15:00Z",
  messages: [
    {
      id: "msg-1",
      role: "user",
      content: "Can I book a cleaning for tomorrow?",
      created_at: "2026-07-23T09:15:00Z",
    },
    {
      id: "msg-2",
      role: "assistant",
      content: "Sure, what time works for you?",
      created_at: "2026-07-23T09:16:00Z",
    },
  ],
};

describe("ConversationDetailView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows a loading state while the request is in flight", () => {
    vi.mocked(getConversation).mockReturnValueOnce(new Promise(() => {}));

    renderWithQueryClient(<ConversationDetailView conversationId="conv-1" />);

    expect(screen.getByRole("status")).toHaveTextContent(/loading conversation/i);
  });

  it("renders the full transcript in order", async () => {
    vi.mocked(getConversation).mockResolvedValueOnce(CONVERSATION_DETAIL);

    renderWithQueryClient(<ConversationDetailView conversationId="conv-1" />);

    expect(await screen.findByText("Can I book a cleaning for tomorrow?")).toBeInTheDocument();
    expect(screen.getByText("Sure, what time works for you?")).toBeInTheDocument();

    const messages = screen.getAllByText(/user|assistant/i).filter((el) => el.tagName === "SPAN");
    const contents = screen
      .getAllByText(/Can I book|Sure, what time/i)
      .map((el) => el.textContent);
    expect(contents).toEqual([
      "Can I book a cleaning for tomorrow?",
      "Sure, what time works for you?",
    ]);
    expect(messages.length).toBeGreaterThan(0);

    expect(screen.getByText("+15551234567")).toBeInTheDocument();
    expect(getConversation).toHaveBeenCalledWith("conv-1");
  });

  it("shows an empty state when the conversation has no messages yet", async () => {
    vi.mocked(getConversation).mockResolvedValueOnce({ ...CONVERSATION_DETAIL, messages: [] });

    renderWithQueryClient(<ConversationDetailView conversationId="conv-1" />);

    expect(await screen.findByText(/doesn't have any messages yet/i)).toBeInTheDocument();
  });

  it("shows a visible error message when the conversation can't be found", async () => {
    vi.mocked(getConversation).mockRejectedValueOnce(new ApiError("Conversation not found", 404));

    renderWithQueryClient(<ConversationDetailView conversationId="missing" />);

    expect(await screen.findByRole("alert")).toHaveTextContent("Conversation not found");
  });
});
