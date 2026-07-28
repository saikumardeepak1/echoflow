"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import {
  KNOWLEDGE_DOCUMENTS_QUERY_KEY,
  KnowledgeDocumentForm,
} from "@/components/knowledge-base/KnowledgeDocumentForm";
import { KnowledgeDocumentList } from "@/components/knowledge-base/KnowledgeDocumentList";
import { ApiError, listKnowledgeDocuments } from "@/lib/api-client";
import type { KnowledgeDocument } from "@/lib/types";

type FormState = { mode: "closed" } | { mode: "create" } | { mode: "edit"; document: KnowledgeDocument };

export default function KnowledgeBasePage() {
  const [formState, setFormState] = useState<FormState>({ mode: "closed" });
  const { data, isPending, isError, error } = useQuery({
    queryKey: KNOWLEDGE_DOCUMENTS_QUERY_KEY,
    queryFn: listKnowledgeDocuments,
  });

  function closeForm() {
    setFormState({ mode: "closed" });
  }

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div className="space-y-2">
          <h1 className="text-2xl font-semibold tracking-tight">Knowledge Base</h1>
          <p className="text-sm text-muted-foreground">
            Documents backing the agent&apos;s answers are managed here.
          </p>
        </div>
        {formState.mode === "closed" ? (
          <Button type="button" onClick={() => setFormState({ mode: "create" })}>
            Add document
          </Button>
        ) : null}
      </div>

      {formState.mode === "create" ? (
        <KnowledgeDocumentForm onCancel={closeForm} onSaved={closeForm} />
      ) : null}
      {formState.mode === "edit" ? (
        <KnowledgeDocumentForm document={formState.document} onCancel={closeForm} onSaved={closeForm} />
      ) : null}

      {isPending ? <p className="text-sm text-muted-foreground">Loading documents...</p> : null}
      {isError ? (
        <p role="alert" className="text-sm font-medium text-destructive">
          {error instanceof ApiError ? error.message : "Failed to load knowledge documents."}
        </p>
      ) : null}
      {data ? (
        <KnowledgeDocumentList
          documents={data}
          onEdit={(document) => setFormState({ mode: "edit", document })}
        />
      ) : null}
    </div>
  );
}
