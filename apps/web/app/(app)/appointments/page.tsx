import { AppointmentsView } from "@/components/appointments/AppointmentsView";

export default function AppointmentsPage() {
  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <h1 className="text-2xl font-semibold tracking-tight">Appointments</h1>
        <p className="text-sm text-muted-foreground">
          Booked appointments, grouped by day. Cancel or reschedule directly from the list.
        </p>
      </div>
      <AppointmentsView />
    </div>
  );
}
