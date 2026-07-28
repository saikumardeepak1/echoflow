import type { Metadata } from "next";

import { LoginForm } from "@/components/auth/LoginForm";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

export const metadata: Metadata = {
  title: "Sign in | EchoFlow",
};

export default function LoginPage() {
  return (
    <main className="flex min-h-screen items-center justify-center p-4">
      <Card className="w-full max-w-sm">
        <CardHeader>
          <CardTitle>Sign in to EchoFlow</CardTitle>
          <CardDescription>
            Enter your email and password to access your organization&apos;s dashboard.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <LoginForm />
        </CardContent>
      </Card>
    </main>
  );
}
