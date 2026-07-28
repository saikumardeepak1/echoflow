"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { ApiError, getConversation } from "@/lib/api-client";
import { cn, formatDateTime } from "@/lib/utils";

export function ConversationDetailView({ conversationId }: { conversationId: string }) {
  const { data, isPending, isError, error } = useQuery({
    queryKey: ["conversation", conversationId],
    queryFn: () => getConversation(conversationId),
  });

  const errorMessage =
    error instanceof ApiError ? error.message : "Something went wrong. Please try again.";

  return (
    <div className="space-y-6">
      <Link href="/conversations" className="text-sm text-muted-foreground hover:underline">
        &larr; Back to conversations
      </Link>

      {isPending ? (
        <p role="status" className="text-sm text-muted-foreground">
          Loading conversation...
        </p>
      ) : isError ? (
        <p role="alert" className="text-sm font-medium text-destructive">
          {errorMessage}
        </p>
      ) : (
        <div className="space-y-6">
          <div className="space-y-2">
            <div className="flex items-center gap-2">
              <Badge variant={data.channel === "voice" ? "default" : "secondary"}>
                {data.channel}
              </Badge>
              <h1 className="text-2xl font-semibold tracking-tight">{data.contact_phone_number}</h1>
              <Badge variant="outline" className="capitalize">
                {data.status}
              </Badge>
            </div>
            <p className="text-sm text-muted-foreground">
              Started {formatDateTime(data.created_at)}
            </p>
          </div>

          {data.messages.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              This conversation doesn&apos;t have any messages yet.
            </p>
          ) : (
            <ul className="space-y-3">
              {data.messages.map((message) => {
                const isUser = message.role === "user";
                return (
                  <li key={message.id} className={cn("flex", isUser ? "justify-start" : "justify-end")}>
                    <Card
                      className={cn(
                        "max-w-[75%]",
                        isUser ? "bg-card" : "bg-secondary text-secondary-foreground",
                      )}
                    >
                      <CardContent className="space-y-1 p-3">
                        <div className="flex items-center justify-between gap-4">
                          <span className="text-xs font-semibold capitalize text-muted-foreground">
                            {message.role}
                          </span>
                          <span className="text-xs text-muted-foreground">
                            {formatDateTime(message.created_at)}
                          </span>
                        </div>
                        <p className="whitespace-pre-wrap text-sm">{message.content}</p>
                      </CardContent>
                    </Card>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
