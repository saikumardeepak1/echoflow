import type { Metadata } from "next";

import { ConversationsView } from "@/components/conversations/ConversationsView";

export const metadata: Metadata = {
  title: "Conversations | EchoFlow",
};

export default function ConversationsPage() {
  return <ConversationsView />;
}
