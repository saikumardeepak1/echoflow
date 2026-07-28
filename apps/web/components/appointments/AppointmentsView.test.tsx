import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AppointmentResponse } from "@/lib/types";

import { AppointmentsView } from "./AppointmentsView";

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
    listAppointments: vi.fn(),
    updateAppointment: vi.fn(),
  };
});

import { ApiError, listAppointments, updateAppointment } from "@/lib/api-client";

const APPOINTMENTS: AppointmentResponse[] = [
  {
    id: "apt-scheduled",
    contact_id: "11111111-2222-3333-4444-555555555555",
    scheduled_at: "2026-07-28T15:00:00Z",
    duration_minutes: 30,
    status: "scheduled",
    notes: "Follow-up cleaning",
    created_at: "2026-07-20T00:00:00Z",
  },
  {
    id: "apt-completed",
    contact_id: "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    scheduled_at: "2026-07-28T18:00:00Z",
    duration_minutes: 45,
    status: "completed",
    notes: null,
    created_at: "2026-07-20T00:00:00Z",
  },
  {
    id: "apt-cancelled",
    contact_id: "99999999-8888-7777-6666-555555555555",
    scheduled_at: "2026-07-29T10:00:00Z",
    duration_minutes: 60,
    status: "cancelled",
    notes: null,
    created_at: "2026-07-20T00:00:00Z",
  },
];

function renderWithQueryClient(ui: ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);
}

describe("AppointmentsView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("shows a loading state while appointments are being fetched", () => {
    vi.mocked(listAppointments).mockReturnValueOnce(new Promise(() => {}));

    renderWithQueryClient(<AppointmentsView />);

    expect(screen.getByText(/loading appointments/i)).toBeInTheDocument();
  });

  it("shows an empty state when there are no appointments", async () => {
    vi.mocked(listAppointments).mockResolvedValueOnce([]);

    renderWithQueryClient(<AppointmentsView />);

    expect(await screen.findByText(/no appointments yet/i)).toBeInTheDocument();
  });

  it("shows a visible error message when the fetch fails", async () => {
    vi.mocked(listAppointments).mockRejectedValueOnce(
      new ApiError("Failed to load appointments", 500),
    );

    renderWithQueryClient(<AppointmentsView />);

    expect(await screen.findByRole("alert")).toHaveTextContent("Failed to load appointments");
  });

  it("groups appointments by day and applies status-specific styling", async () => {
    vi.mocked(listAppointments).mockResolvedValueOnce(APPOINTMENTS);

    renderWithQueryClient(<AppointmentsView />);

    expect(await screen.findByText(/July 28, 2026/)).toBeInTheDocument();
    expect(screen.getByText(/July 29, 2026/)).toBeInTheDocument();

    // Two appointments grouped under the 28th, one under the 29th.
    expect(screen.getByText(/3:00 PM/)).toBeInTheDocument();
    expect(screen.getByText(/6:00 PM/)).toBeInTheDocument();
    expect(screen.getByText(/10:00 AM/)).toBeInTheDocument();

    const scheduledBadge = screen.getByText("Scheduled");
    const completedBadge = screen.getByText("Completed");
    const cancelledBadge = screen.getByText("Cancelled");

    expect(scheduledBadge).toHaveAttribute("data-status", "scheduled");
    expect(completedBadge).toHaveAttribute("data-status", "completed");
    expect(cancelledBadge).toHaveAttribute("data-status", "cancelled");

    expect(scheduledBadge.className).toContain("blue");
    expect(completedBadge.className).toContain("emerald");
  });

  it("only offers cancel/reschedule actions for scheduled appointments", async () => {
    vi.mocked(listAppointments).mockResolvedValueOnce(APPOINTMENTS);

    renderWithQueryClient(<AppointmentsView />);

    await screen.findByText(/July 28, 2026/);

    expect(screen.getAllByRole("button", { name: /cancel/i })).toHaveLength(1);
    expect(screen.getAllByRole("button", { name: /reschedule/i })).toHaveLength(1);
  });

  it("cancels a scheduled appointment via PATCH after confirmation", async () => {
    vi.mocked(listAppointments).mockResolvedValue(APPOINTMENTS);
    vi.mocked(updateAppointment).mockResolvedValueOnce({
      ...APPOINTMENTS[0],
      status: "cancelled",
    });
    vi.spyOn(window, "confirm").mockReturnValue(true);

    renderWithQueryClient(<AppointmentsView />);

    await screen.findByText(/July 28, 2026/);
    fireEvent.click(screen.getByRole("button", { name: /cancel/i }));

    await waitFor(() =>
      expect(updateAppointment).toHaveBeenCalledWith("apt-scheduled", { status: "cancelled" }),
    );
  });

  it("does not cancel when the confirmation is dismissed", async () => {
    vi.mocked(listAppointments).mockResolvedValue(APPOINTMENTS);
    vi.spyOn(window, "confirm").mockReturnValue(false);

    renderWithQueryClient(<AppointmentsView />);

    await screen.findByText(/July 28, 2026/);
    fireEvent.click(screen.getByRole("button", { name: /cancel/i }));

    expect(updateAppointment).not.toHaveBeenCalled();
  });

  it("reschedules an appointment via PATCH with the new date and time", async () => {
    vi.mocked(listAppointments).mockResolvedValue(APPOINTMENTS);
    vi.mocked(updateAppointment).mockResolvedValueOnce({
      ...APPOINTMENTS[0],
      scheduled_at: "2026-08-01T10:00:00Z",
    });

    renderWithQueryClient(<AppointmentsView />);

    await screen.findByText(/July 28, 2026/);
    fireEvent.click(screen.getByRole("button", { name: /reschedule/i }));

    const input = await screen.findByLabelText(/new date and time/i);
    fireEvent.change(input, { target: { value: "2026-08-01T10:00" } });
    fireEvent.click(screen.getByRole("button", { name: /^save$/i }));

    await waitFor(() =>
      expect(updateAppointment).toHaveBeenCalledWith("apt-scheduled", {
        scheduled_at: "2026-08-01T10:00:00Z",
      }),
    );
  });

  it("shows an error message inline when a PATCH action fails", async () => {
    vi.mocked(listAppointments).mockResolvedValue(APPOINTMENTS);
    vi.mocked(updateAppointment).mockRejectedValueOnce(
      new ApiError("Appointment could not be updated", 409),
    );
    vi.spyOn(window, "confirm").mockReturnValue(true);

    renderWithQueryClient(<AppointmentsView />);

    await screen.findByText(/July 28, 2026/);
    fireEvent.click(screen.getByRole("button", { name: /cancel/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Appointment could not be updated");
  });
});
