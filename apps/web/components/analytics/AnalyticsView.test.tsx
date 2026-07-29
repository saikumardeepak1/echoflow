import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import type { ReactElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AnalyticsOverviewResponse } from "@/lib/types";

import { AnalyticsView } from "./AnalyticsView";

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
    getAnalyticsOverview: vi.fn(),
  };
});

import { ApiError, getAnalyticsOverview } from "@/lib/api-client";

const OVERVIEW: AnalyticsOverviewResponse = {
  start_date: "2026-06-29",
  end_date: "2026-07-28",
  call_volume: 42,
  sms_volume: 108,
  appointments_booked: 17,
  average_conversation_length: 3.5,
};

const EMPTY_OVERVIEW: AnalyticsOverviewResponse = {
  start_date: "2026-06-29",
  end_date: "2026-07-28",
  call_volume: 0,
  sms_volume: 0,
  appointments_booked: 0,
  average_conversation_length: 0,
};

function renderWithQueryClient(ui: ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

describe("AnalyticsView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows a loading state while the overview is being fetched", () => {
    vi.mocked(getAnalyticsOverview).mockReturnValueOnce(new Promise(() => {}));

    renderWithQueryClient(<AnalyticsView />);

    expect(screen.getByRole("status")).toHaveTextContent(/loading analytics/i);
  });

  it("shows an empty state when there is no activity in the range", async () => {
    vi.mocked(getAnalyticsOverview).mockResolvedValueOnce(EMPTY_OVERVIEW);

    renderWithQueryClient(<AnalyticsView />);

    expect(await screen.findByText(/no activity recorded/i)).toBeInTheDocument();
  });

  it("shows a visible error message when the fetch fails", async () => {
    vi.mocked(getAnalyticsOverview).mockRejectedValueOnce(
      new ApiError("Failed to load analytics", 500),
    );

    renderWithQueryClient(<AnalyticsView />);

    expect(await screen.findByRole("alert")).toHaveTextContent("Failed to load analytics");
  });

  it("renders the fetched metrics", async () => {
    vi.mocked(getAnalyticsOverview).mockResolvedValueOnce(OVERVIEW);

    renderWithQueryClient(<AnalyticsView />);

    expect(await screen.findByRole("img", { name: "Calls: 42" })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "SMS: 108" })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Appointments booked: 17" })).toBeInTheDocument();
    expect(screen.getByText(/3\.5/)).toBeInTheDocument();
  });

  it("re-fetches with the new range when the date inputs change", async () => {
    vi.mocked(getAnalyticsOverview).mockResolvedValue(OVERVIEW);

    renderWithQueryClient(<AnalyticsView />);

    await screen.findByRole("img", { name: "Calls: 42" });
    expect(getAnalyticsOverview).toHaveBeenCalledTimes(1);

    fireEvent.change(screen.getByLabelText(/start date/i), {
      target: { value: "2026-07-01" },
    });
    fireEvent.change(screen.getByLabelText(/end date/i), {
      target: { value: "2026-07-15" },
    });

    await screen.findByRole("img", { name: "Calls: 42" });

    expect(getAnalyticsOverview).toHaveBeenCalledWith({
      start_date: "2026-07-01",
      end_date: "2026-07-15",
    });
  });

  it("shows a validation message instead of fetching when the range is invalid", async () => {
    vi.mocked(getAnalyticsOverview).mockResolvedValue(OVERVIEW);

    renderWithQueryClient(<AnalyticsView />);

    fireEvent.change(screen.getByLabelText(/start date/i), {
      target: { value: "2026-07-20" },
    });
    fireEvent.change(screen.getByLabelText(/end date/i), {
      target: { value: "2026-07-01" },
    });

    expect(
      await screen.findByText(/end date must not be before start date/i),
    ).toBeInTheDocument();
  });
});
