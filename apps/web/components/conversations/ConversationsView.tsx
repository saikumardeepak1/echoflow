"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { ApiError, listConversations } from "@/lib/api-client";
import type { ConversationChannel } from "@/lib/types";
import { cn, formatDateTime } from "@/lib/utils";

const CHANNEL_FILTERS: { label: string; value: ConversationChannel | "all" }[] = [
  { label: "All", value: "all" },
  { label: "Voice", value: "voice" },
  { label: "SMS", value: "sms" },
];

export function ConversationsView() {
  const [channel, setChannel] = useState<ConversationChannel | "all">("all");

  const { data, isPending, isError, error } = useQuery({
    queryKey: ["conversations", channel],
    queryFn: () => listConversations(channel === "all" ? undefined : { channel }),
  });

  const errorMessage =
    error instanceof ApiError ? error.message : "Something went wrong. Please try again.";

  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <h1 className="text-2xl font-semibold tracking-tight">Conversations</h1>
        <p className="text-sm text-muted-foreground">
          Call and text transcripts from every channel, most recent first.
        </p>
      </div>

      <div role="group" aria-label="Filter by channel" className="flex gap-2">
        {CHANNEL_FILTERS.map((filter) => (
          <Button
            key={filter.value}
            type="button"
            variant={channel === filter.value ? "default" : "outline"}
            size="sm"
            aria-pressed={channel === filter.value}
            onClick={() => setChannel(filter.value)}
          >
            {filter.label}
          </Button>
        ))}
      </div>

      {isPending ? (
        <p role="status" className="text-sm text-muted-foreground">
          Loading conversations...
        </p>
      ) : isError ? (
        <p role="alert" className="text-sm font-medium text-destructive">
          {errorMessage}
        </p>
      ) : data.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No conversations found{channel === "all" ? "" : ` for ${channel}`}.
        </p>
      ) : (
        <ul className="space-y-3">
          {data.map((conversation) => (
            <li key={conversation.id}>
              <Link href={`/conversations/${conversation.id}`} className="block">
                <Card className="transition-colors hover:bg-accent">
                  <CardContent className="flex items-center justify-between gap-4 p-4">
                    <div className="space-y-1">
                      <div className="flex items-center gap-2">
                        <Badge variant={conversation.channel === "voice" ? "default" : "secondary"}>
                          {conversation.channel}
                        </Badge>
                        <span className="font-medium">{conversation.contact_phone_number}</span>
                        <Badge variant="outline" className={cn("capitalize")}>
                          {conversation.status}
                        </Badge>
                      </div>
                    </div>
                    <span className="whitespace-nowrap text-sm text-muted-foreground">
                      {formatDateTime(conversation.created_at)}
                    </span>
                  </CardContent>
                </Card>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
