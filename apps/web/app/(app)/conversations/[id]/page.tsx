import type { Metadata } from "next";

import { ConversationDetailView } from "@/components/conversations/ConversationDetailView";

export const metadata: Metadata = {
  title: "Conversation | EchoFlow",
};

export default function ConversationDetailPage({ params }: { params: { id: string } }) {
  return <ConversationDetailView conversationId={params.id} />;
}
