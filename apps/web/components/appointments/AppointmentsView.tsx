"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { ApiError, listAppointments, updateAppointment } from "@/lib/api-client";
import type { AppointmentResponse, AppointmentStatus, AppointmentUpdateRequest } from "@/lib/types";
import { cn } from "@/lib/utils";

const STATUS_STYLES: Record<AppointmentStatus, string> = {
  scheduled: "bg-blue-100 text-blue-800 dark:bg-blue-900/40 dark:text-blue-300",
  completed: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300",
  cancelled: "bg-muted text-muted-foreground",
};

const STATUS_LABELS: Record<AppointmentStatus, string> = {
  scheduled: "Scheduled",
  completed: "Completed",
  cancelled: "Cancelled",
};

function StatusBadge({ status }: { status: AppointmentStatus }) {
  return (
    <span
      data-status={status}
      className={cn(
        "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium",
        STATUS_STYLES[status],
      )}
    >
      {STATUS_LABELS[status]}
    </span>
  );
}

// scheduled_at comes back as an ISO 8601 UTC timestamp (see
// AppointmentResponse in apps/api/app/schemas/appointment.py). Formatting
// and grouping against the UTC calendar day, rather than the viewer's local
// timezone, keeps the day headers and reschedule input in sync with the
// value shown for each appointment and keeps this deterministic in tests.
const dayFormatter = new Intl.DateTimeFormat("en-US", {
  weekday: "long",
  month: "long",
  day: "numeric",
  year: "numeric",
  timeZone: "UTC",
});

const timeFormatter = new Intl.DateTimeFormat("en-US", {
  hour: "numeric",
  minute: "2-digit",
  timeZone: "UTC",
});

function dayKey(isoDate: string): string {
  return isoDate.slice(0, 10);
}

function groupByDay(appointments: AppointmentResponse[]): Array<[string, AppointmentResponse[]]> {
  const sorted = [...appointments].sort(
    (a, b) => new Date(a.scheduled_at).getTime() - new Date(b.scheduled_at).getTime(),
  );
  const groups = new Map<string, AppointmentResponse[]>();
  for (const appointment of sorted) {
    const key = dayKey(appointment.scheduled_at);
    const existing = groups.get(key);
    if (existing) {
      existing.push(appointment);
    } else {
      groups.set(key, [appointment]);
    }
  }
  return Array.from(groups.entries());
}

function toDateTimeLocalValue(isoDate: string): string {
  // <input type="datetime-local"> expects "YYYY-MM-DDTHH:mm" with no
  // timezone suffix. We pre-fill it with the appointment's UTC wall-clock
  // time so the form starts from what's currently on the calendar.
  return isoDate.slice(0, 16);
}

interface AppointmentRowProps {
  appointment: AppointmentResponse;
}

function AppointmentRow({ appointment }: AppointmentRowProps) {
  const queryClient = useQueryClient();
  const [isRescheduling, setIsRescheduling] = useState(false);
  const [newScheduledAt, setNewScheduledAt] = useState(() =>
    toDateTimeLocalValue(appointment.scheduled_at),
  );

  const mutation = useMutation({
    mutationFn: (payload: AppointmentUpdateRequest) => updateAppointment(appointment.id, payload),
    onSuccess: () => {
      setIsRescheduling(false);
      void queryClient.invalidateQueries({ queryKey: ["appointments"] });
    },
  });

  const errorMessage =
    mutation.error instanceof ApiError
      ? mutation.error.message
      : mutation.isError
        ? "Something went wrong. Please try again."
        : null;

  function handleCancel() {
    if (!window.confirm("Cancel this appointment?")) return;
    mutation.mutate({ status: "cancelled" });
  }

  function handleRescheduleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!newScheduledAt) return;
    mutation.mutate({ scheduled_at: `${newScheduledAt}:00Z` });
  }

  const canAct = appointment.status === "scheduled";

  return (
    <li className="flex flex-col gap-3 border-b py-4 last:border-b-0">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="font-medium">
            {timeFormatter.format(new Date(appointment.scheduled_at))} &middot;{" "}
            {appointment.duration_minutes} min
          </p>
          <p className="text-sm text-muted-foreground">
            Contact {appointment.contact_id.slice(0, 8)}
          </p>
          {appointment.notes ? (
            <p className="mt-1 text-sm text-muted-foreground">{appointment.notes}</p>
          ) : null}
        </div>
        <div className="flex items-center gap-2">
          <StatusBadge status={appointment.status} />
          {canAct ? (
            <>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => setIsRescheduling((prev) => !prev)}
              >
                Reschedule
              </Button>
              <Button
                type="button"
                variant="destructive"
                size="sm"
                onClick={handleCancel}
                disabled={mutation.isPending}
              >
                Cancel
              </Button>
            </>
          ) : null}
        </div>
      </div>
      {isRescheduling ? (
        <form
          onSubmit={handleRescheduleSubmit}
          className="flex flex-wrap items-end gap-2 rounded-md border bg-muted/40 p-3"
        >
          <div className="space-y-1">
            <label
              htmlFor={`reschedule-${appointment.id}`}
              className="text-xs font-medium text-muted-foreground"
            >
              New date and time
            </label>
            <Input
              id={`reschedule-${appointment.id}`}
              type="datetime-local"
              value={newScheduledAt}
              onChange={(event) => setNewScheduledAt(event.target.value)}
              required
            />
          </div>
          <Button type="submit" size="sm" disabled={mutation.isPending}>
            {mutation.isPending ? "Saving..." : "Save"}
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => setIsRescheduling(false)}
          >
            Close
          </Button>
        </form>
      ) : null}
      {errorMessage ? (
        <p role="alert" className="text-sm font-medium text-destructive">
          {errorMessage}
        </p>
      ) : null}
    </li>
  );
}

export function AppointmentsView() {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["appointments"],
    queryFn: listAppointments,
  });

  if (isLoading) {
    return <p className="text-sm text-muted-foreground">Loading appointments...</p>;
  }

  if (isError) {
    const message = error instanceof ApiError ? error.message : "Failed to load appointments.";
    return (
      <p role="alert" className="text-sm font-medium text-destructive">
        {message}
      </p>
    );
  }

  const appointments = data ?? [];

  if (appointments.length === 0) {
    return <p className="text-sm text-muted-foreground">No appointments yet.</p>;
  }

  const groups = groupByDay(appointments);

  return (
    <div className="space-y-6">
      {groups.map(([day, dayAppointments]) => (
        <Card key={day}>
          <CardHeader>
            <CardTitle className="text-base">
              {dayFormatter.format(new Date(`${day}T00:00:00Z`))}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <ul>
              {dayAppointments.map((appointment) => (
                <AppointmentRow key={appointment.id} appointment={appointment} />
              ))}
            </ul>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
