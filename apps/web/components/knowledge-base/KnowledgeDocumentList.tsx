"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { KNOWLEDGE_DOCUMENTS_QUERY_KEY } from "@/components/knowledge-base/KnowledgeDocumentForm";
import { ApiError, deleteKnowledgeDocument } from "@/lib/api-client";
import type { KnowledgeDocument } from "@/lib/types";

interface KnowledgeDocumentListProps {
  documents: KnowledgeDocument[];
  onEdit: (document: KnowledgeDocument) => void;
}

export function KnowledgeDocumentList({ documents, onEdit }: KnowledgeDocumentListProps) {
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null);
  const queryClient = useQueryClient();

  const deleteMutation = useMutation({
    mutationFn: (id: string) => deleteKnowledgeDocument(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: KNOWLEDGE_DOCUMENTS_QUERY_KEY });
      setPendingDeleteId(null);
    },
  });

  if (documents.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        No knowledge documents yet. Add one to give the agent something to answer from.
      </p>
    );
  }

  const errorMessage =
    deleteMutation.error instanceof ApiError
      ? deleteMutation.error.message
      : deleteMutation.isError
        ? "Something went wrong. Please try again."
        : null;

  return (
    <div className="space-y-3">
      {errorMessage ? (
        <p role="alert" className="text-sm font-medium text-destructive">
          {errorMessage}
        </p>
      ) : null}
      <ul className="space-y-3">
        {documents.map((document) => {
          const isPendingDelete = pendingDeleteId === document.id;
          const isDeletingThis = deleteMutation.isPending && deleteMutation.variables === document.id;

          return (
            <li key={document.id}>
              <Card>
                <CardHeader className="flex flex-row items-start justify-between space-y-0">
                  <CardTitle className="text-base">{document.title}</CardTitle>
                  <div className="flex shrink-0 gap-2">
                    {isPendingDelete ? (
                      <>
                        <span className="self-center text-sm text-muted-foreground">
                          Delete this document?
                        </span>
                        <Button
                          type="button"
                          variant="destructive"
                          size="sm"
                          disabled={isDeletingThis}
                          onClick={() => deleteMutation.mutate(document.id)}
                        >
                          {isDeletingThis ? "Deleting..." : "Confirm delete"}
                        </Button>
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          disabled={isDeletingThis}
                          onClick={() => setPendingDeleteId(null)}
                        >
                          Cancel
                        </Button>
                      </>
                    ) : (
                      <>
                        <Button type="button" variant="outline" size="sm" onClick={() => onEdit(document)}>
                          Edit
                        </Button>
                        <Button
                          type="button"
                          variant="destructive"
                          size="sm"
                          onClick={() => setPendingDeleteId(document.id)}
                        >
                          Delete
                        </Button>
                      </>
                    )}
                  </div>
                </CardHeader>
                <CardContent>
                  <p className="whitespace-pre-wrap text-sm text-muted-foreground">{document.content}</p>
                </CardContent>
              </Card>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
