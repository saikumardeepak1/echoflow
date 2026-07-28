"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { ApiError, createKnowledgeDocument, deleteKnowledgeDocument } from "@/lib/api-client";
import type { KnowledgeDocument } from "@/lib/types";

export const KNOWLEDGE_DOCUMENTS_QUERY_KEY = ["knowledge-documents"] as const;

interface KnowledgeDocumentFormProps {
  /** When present, the form edits this document; otherwise it creates a new one. */
  document?: KnowledgeDocument | null;
  onCancel: () => void;
  onSaved: () => void;
}

export function KnowledgeDocumentForm({ document, onCancel, onSaved }: KnowledgeDocumentFormProps) {
  const isEditing = Boolean(document);
  const [title, setTitle] = useState(document?.title ?? "");
  const [content, setContent] = useState(document?.content ?? "");
  const queryClient = useQueryClient();

  const mutation = useMutation({
    mutationFn: async () => {
      // The API only exposes create and delete, no update endpoint (see
      // apps/api/app/api/knowledge_documents.py). An edit is therefore
      // applied as create-the-revision-then-remove-the-original, in that
      // order, so a failed create never destroys the existing document.
      await createKnowledgeDocument({ title, content });
      if (document) {
        await deleteKnowledgeDocument(document.id);
      }
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: KNOWLEDGE_DOCUMENTS_QUERY_KEY });
      onSaved();
    },
  });

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    mutation.reset();
    mutation.mutate();
  }

  const errorMessage =
    mutation.error instanceof ApiError
      ? mutation.error.message
      : mutation.isError
        ? "Something went wrong. Please try again."
        : null;

  return (
    <form onSubmit={handleSubmit} noValidate className="space-y-4">
      <div className="space-y-2">
        <Label htmlFor="title">Title</Label>
        <Input
          id="title"
          name="title"
          required
          maxLength={500}
          value={title}
          onChange={(event) => setTitle(event.target.value)}
        />
      </div>
      <div className="space-y-2">
        <Label htmlFor="content">Content</Label>
        <Textarea
          id="content"
          name="content"
          required
          value={content}
          onChange={(event) => setContent(event.target.value)}
        />
      </div>
      {errorMessage ? (
        <p role="alert" className="text-sm font-medium text-destructive">
          {errorMessage}
        </p>
      ) : null}
      <div className="flex gap-2">
        <Button type="submit" disabled={mutation.isPending}>
          {mutation.isPending ? "Saving..." : isEditing ? "Save changes" : "Add document"}
        </Button>
        <Button type="button" variant="outline" onClick={onCancel} disabled={mutation.isPending}>
          Cancel
        </Button>
      </div>
    </form>
  );
}
