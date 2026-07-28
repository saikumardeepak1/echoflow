import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactElement } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { KnowledgeDocumentList } from "./KnowledgeDocumentList";

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
    deleteKnowledgeDocument: vi.fn(),
  };
});

import { ApiError, deleteKnowledgeDocument } from "@/lib/api-client";

function renderWithQueryClient(ui: ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

const documents = [
  {
    id: "doc-1",
    title: "Cancellation policy",
    content: "Cancel at least 24 hours in advance.",
    created_at: "2026-07-23T09:15:00Z",
  },
  {
    id: "doc-2",
    title: "Business hours",
    content: "Open 9am to 5pm, Monday through Friday.",
    created_at: "2026-07-22T09:15:00Z",
  },
];

describe("KnowledgeDocumentList", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders every document's title and content", () => {
    renderWithQueryClient(<KnowledgeDocumentList documents={documents} onEdit={vi.fn()} />);

    expect(screen.getByText("Cancellation policy")).toBeInTheDocument();
    expect(screen.getByText("Cancel at least 24 hours in advance.")).toBeInTheDocument();
    expect(screen.getByText("Business hours")).toBeInTheDocument();
    expect(screen.getByText("Open 9am to 5pm, Monday through Friday.")).toBeInTheDocument();
  });

  it("renders an empty state when there are no documents", () => {
    renderWithQueryClient(<KnowledgeDocumentList documents={[]} onEdit={vi.fn()} />);

    expect(screen.getByText(/no knowledge documents yet/i)).toBeInTheDocument();
  });

  it("calls onEdit with the document when its Edit button is clicked", () => {
    const onEdit = vi.fn();
    renderWithQueryClient(<KnowledgeDocumentList documents={documents} onEdit={onEdit} />);

    fireEvent.click(screen.getAllByRole("button", { name: /^edit$/i })[0]);

    expect(onEdit).toHaveBeenCalledWith(documents[0]);
  });

  it("does not delete immediately: clicking Delete asks for confirmation first", () => {
    renderWithQueryClient(<KnowledgeDocumentList documents={documents} onEdit={vi.fn()} />);

    fireEvent.click(screen.getAllByRole("button", { name: /^delete$/i })[0]);

    expect(screen.getByText(/delete this document\?/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /confirm delete/i })).toBeInTheDocument();
    expect(deleteKnowledgeDocument).not.toHaveBeenCalled();
  });

  it("cancels the pending delete without calling the API", () => {
    renderWithQueryClient(<KnowledgeDocumentList documents={documents} onEdit={vi.fn()} />);

    fireEvent.click(screen.getAllByRole("button", { name: /^delete$/i })[0]);
    fireEvent.click(screen.getByRole("button", { name: /cancel/i }));

    expect(screen.queryByText(/delete this document\?/i)).not.toBeInTheDocument();
    expect(deleteKnowledgeDocument).not.toHaveBeenCalled();
  });

  it("deletes the document only after the confirmation step is confirmed", async () => {
    vi.mocked(deleteKnowledgeDocument).mockResolvedValueOnce(undefined);
    renderWithQueryClient(<KnowledgeDocumentList documents={documents} onEdit={vi.fn()} />);

    fireEvent.click(screen.getAllByRole("button", { name: /^delete$/i })[0]);
    fireEvent.click(screen.getByRole("button", { name: /confirm delete/i }));

    await waitFor(() => expect(deleteKnowledgeDocument).toHaveBeenCalledWith("doc-1"));
  });

  it("shows a visible error message when deletion fails", async () => {
    vi.mocked(deleteKnowledgeDocument).mockRejectedValueOnce(new ApiError("Not found", 404));
    renderWithQueryClient(<KnowledgeDocumentList documents={documents} onEdit={vi.fn()} />);

    fireEvent.click(screen.getAllByRole("button", { name: /^delete$/i })[0]);
    fireEvent.click(screen.getByRole("button", { name: /confirm delete/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Not found");
  });
});
