import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactElement } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { KnowledgeDocumentForm } from "./KnowledgeDocumentForm";

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
    createKnowledgeDocument: vi.fn(),
    deleteKnowledgeDocument: vi.fn(),
  };
});

import { ApiError, createKnowledgeDocument, deleteKnowledgeDocument } from "@/lib/api-client";

function renderWithQueryClient(ui: ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

const document = {
  id: "doc-1",
  title: "Cancellation policy",
  content: "Cancel at least 24 hours in advance.",
  created_at: "2026-07-23T09:15:00Z",
};

describe("KnowledgeDocumentForm", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders empty title and content fields in create mode", () => {
    renderWithQueryClient(<KnowledgeDocumentForm onCancel={vi.fn()} onSaved={vi.fn()} />);

    expect(screen.getByLabelText(/title/i)).toHaveValue("");
    expect(screen.getByLabelText(/content/i)).toHaveValue("");
    expect(screen.getByRole("button", { name: /add document/i })).toBeInTheDocument();
  });

  it("submits the entered title and content to create a new document", async () => {
    vi.mocked(createKnowledgeDocument).mockResolvedValueOnce({
      id: "doc-2",
      title: "Hours",
      content: "We're open 9 to 5.",
      created_at: "2026-07-24T00:00:00Z",
    });
    const onSaved = vi.fn();

    renderWithQueryClient(<KnowledgeDocumentForm onCancel={vi.fn()} onSaved={onSaved} />);

    fireEvent.change(screen.getByLabelText(/title/i), { target: { value: "Hours" } });
    fireEvent.change(screen.getByLabelText(/content/i), { target: { value: "We're open 9 to 5." } });
    fireEvent.click(screen.getByRole("button", { name: /add document/i }));

    await waitFor(() =>
      expect(createKnowledgeDocument).toHaveBeenCalledWith({
        title: "Hours",
        content: "We're open 9 to 5.",
      }),
    );
    expect(deleteKnowledgeDocument).not.toHaveBeenCalled();
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
  });

  it("pre-fills the form with the document's title and content in edit mode", () => {
    renderWithQueryClient(
      <KnowledgeDocumentForm document={document} onCancel={vi.fn()} onSaved={vi.fn()} />,
    );

    expect(screen.getByLabelText(/title/i)).toHaveValue("Cancellation policy");
    expect(screen.getByLabelText(/content/i)).toHaveValue("Cancel at least 24 hours in advance.");
    expect(screen.getByRole("button", { name: /save changes/i })).toBeInTheDocument();
  });

  it("saves an edit by creating the revised document and removing the original", async () => {
    vi.mocked(createKnowledgeDocument).mockResolvedValueOnce({
      id: "doc-3",
      title: "Cancellation policy (updated)",
      content: "Cancel at least 48 hours in advance.",
      created_at: "2026-07-25T00:00:00Z",
    });
    vi.mocked(deleteKnowledgeDocument).mockResolvedValueOnce(undefined);
    const onSaved = vi.fn();

    renderWithQueryClient(
      <KnowledgeDocumentForm document={document} onCancel={vi.fn()} onSaved={onSaved} />,
    );

    fireEvent.change(screen.getByLabelText(/title/i), {
      target: { value: "Cancellation policy (updated)" },
    });
    fireEvent.change(screen.getByLabelText(/content/i), {
      target: { value: "Cancel at least 48 hours in advance." },
    });
    fireEvent.click(screen.getByRole("button", { name: /save changes/i }));

    await waitFor(() =>
      expect(createKnowledgeDocument).toHaveBeenCalledWith({
        title: "Cancellation policy (updated)",
        content: "Cancel at least 48 hours in advance.",
      }),
    );
    await waitFor(() => expect(deleteKnowledgeDocument).toHaveBeenCalledWith("doc-1"));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
  });

  it("calls onCancel without submitting when Cancel is clicked", () => {
    const onCancel = vi.fn();
    renderWithQueryClient(<KnowledgeDocumentForm onCancel={onCancel} onSaved={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: /cancel/i }));

    expect(onCancel).toHaveBeenCalled();
    expect(createKnowledgeDocument).not.toHaveBeenCalled();
  });

  it("shows a visible error message when the API call fails, instead of failing silently", async () => {
    vi.mocked(createKnowledgeDocument).mockRejectedValueOnce(
      new ApiError("Title is required", 422),
    );
    const onSaved = vi.fn();

    renderWithQueryClient(<KnowledgeDocumentForm onCancel={vi.fn()} onSaved={onSaved} />);

    fireEvent.change(screen.getByLabelText(/title/i), { target: { value: "Hours" } });
    fireEvent.change(screen.getByLabelText(/content/i), { target: { value: "Open 9 to 5." } });
    fireEvent.click(screen.getByRole("button", { name: /add document/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Title is required");
    expect(onSaved).not.toHaveBeenCalled();
  });
});
