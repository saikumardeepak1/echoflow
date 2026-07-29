"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError, getAnalyticsOverview } from "@/lib/api-client";

/** "YYYY-MM-DD" in the viewer's local calendar, the format `<input type="date">` expects. */
function toDateInputValue(date: Date): string {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function defaultStartDate(): string {
  const date = new Date();
  date.setDate(date.getDate() - 29);
  return toDateInputValue(date);
}

function defaultEndDate(): string {
  return toDateInputValue(new Date());
}

interface VolumeBarProps {
  label: string;
  value: number;
  maxValue: number;
}

function VolumeBar({ label, value, maxValue }: VolumeBarProps) {
  const widthPercent = maxValue === 0 ? 0 : Math.round((value / maxValue) * 100);
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between text-sm">
        <span className="font-medium">{label}</span>
        <span className="text-muted-foreground">{value}</span>
      </div>
      <div className="h-2.5 w-full overflow-hidden rounded-full bg-muted">
        <div
          role="img"
          aria-label={`${label}: ${value}`}
          className="h-full rounded-full bg-primary transition-all"
          style={{ width: `${widthPercent}%` }}
        />
      </div>
    </div>
  );
}

export function AnalyticsView() {
  const [startDate, setStartDate] = useState(defaultStartDate);
  const [endDate, setEndDate] = useState(defaultEndDate);

  const isRangeInvalid = endDate < startDate;

  const { data, isPending, isError, error } = useQuery({
    queryKey: ["analytics-overview", startDate, endDate],
    queryFn: () => getAnalyticsOverview({ start_date: startDate, end_date: endDate }),
    enabled: !isRangeInvalid,
  });

  const errorMessage =
    error instanceof ApiError ? error.message : "Something went wrong. Please try again.";

  const hasActivity =
    !!data && (data.call_volume > 0 || data.sms_volume > 0 || data.appointments_booked > 0);

  const maxVolume = data ? Math.max(data.call_volume, data.sms_volume, data.appointments_booked) : 0;

  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <h1 className="text-2xl font-semibold tracking-tight">Analytics</h1>
        <p className="text-sm text-muted-foreground">
          Call/SMS volume, appointments booked, and conversation length over a date range.
        </p>
      </div>

      <form
        aria-label="Date range"
        className="flex flex-wrap items-end gap-4"
        onSubmit={(event) => event.preventDefault()}
      >
        <div className="space-y-1">
          <Label htmlFor="analytics-start-date">Start date</Label>
          <Input
            id="analytics-start-date"
            type="date"
            value={startDate}
            max={endDate}
            onChange={(event) => setStartDate(event.target.value)}
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="analytics-end-date">End date</Label>
          <Input
            id="analytics-end-date"
            type="date"
            value={endDate}
            min={startDate}
            onChange={(event) => setEndDate(event.target.value)}
          />
        </div>
      </form>

      {isRangeInvalid ? (
        <p role="alert" className="text-sm font-medium text-destructive">
          End date must not be before start date.
        </p>
      ) : isPending ? (
        <p role="status" className="text-sm text-muted-foreground">
          Loading analytics...
        </p>
      ) : isError ? (
        <p role="alert" className="text-sm font-medium text-destructive">
          {errorMessage}
        </p>
      ) : !hasActivity ? (
        <p className="text-sm text-muted-foreground">
          No activity recorded for this date range.
        </p>
      ) : (
        <div className="grid gap-6 md:grid-cols-2">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Volume</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <VolumeBar label="Calls" value={data.call_volume} maxValue={maxVolume} />
              <VolumeBar label="SMS" value={data.sms_volume} maxValue={maxVolume} />
              <VolumeBar
                label="Appointments booked"
                value={data.appointments_booked}
                maxValue={maxVolume}
              />
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">Average conversation length</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-3xl font-semibold">
                {data.average_conversation_length.toFixed(1)}
                <span className="ml-1 text-base font-normal text-muted-foreground">
                  messages / conversation
                </span>
              </p>
            </CardContent>
          </Card>
        </div>
      )}
    </div>
  );
}
