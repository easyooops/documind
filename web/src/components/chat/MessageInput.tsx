"use client";

import React, { useState, useRef } from "react";
import {
  Send,
  Paperclip,
  ChevronDown,
  FileText,
  Presentation,
  FileSpreadsheet,
  Code2,
  Globe,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { useSessionStore } from "@/stores/session";
import { useDocumentStore } from "@/stores/document";
import { useUserStore } from "@/stores/user";
import { cn } from "@/lib/utils";
import {
  createSession,
  streamGeneration,
  uploadTemplate,
  getJobStatus,
} from "@/lib/api";
import {
  FORMAT_LABELS,
  type DocumentFormat,
  type Template,
} from "@/types";
import { generateId } from "@/lib/utils";
import { useTranslation } from "@/hooks/useTranslation";

const FORMAT_ICONS: Record<DocumentFormat, React.ElementType> = {
  pptx: Presentation,
  docx: FileText,
  md: Code2,
  html: Globe,
  hwp: FileText,
  xlsx: FileSpreadsheet,
  pdf: FileText,
};

const FORMATS: DocumentFormat[] = ["pptx", "docx", "pdf", "md", "html", "xlsx", "hwp"];

export function MessageInput() {
  const [text, setText] = useState("");
  const [showFormats, setShowFormats] = useState(false);
  const [attachedTemplate, setAttachedTemplate] = useState<Template | null>(null);
  const [uploading, setUploading] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const cancelStreamRef = useRef<(() => void) | null>(null);

  const { user } = useUserStore();
  const {
    currentSessionId,
    selectedFormat,
    setSelectedFormat,
    setCurrentSession,
    addSession,
    addMessage,
    setGenerating,
    setPhase,
    setProgress,
    setCurrentNode,
    addCompletedNode,
    clearProgress,
    isGenerating,
  } = useSessionStore();
  const { setCurrentJob, openPanel } = useDocumentStore();
  const { t } = useTranslation();

  const FormatIcon = FORMAT_ICONS[selectedFormat];

  const handleSubmit = async (e?: React.FormEvent) => {
    e?.preventDefault();
    if (!text.trim() || isGenerating) return;

    const query = text.trim();
    setText("");
    clearProgress();
    setGenerating(true);
    setPhase("planning");
    setProgress(0);

    const userMessage = {
      id: generateId(),
      role: "user" as const,
      content: query,
      createdAt: new Date().toISOString(),
    };
    addMessage(userMessage);

    try {
      let sessionId = currentSessionId;
      if (!sessionId) {
        const session = await createSession(user?.id);
        sessionId = session.id;
        setCurrentSession(sessionId);
        addSession({
          id: sessionId,
          title: query.slice(0, 50),
          format: selectedFormat,
          createdAt: session.created_at,
          updatedAt: session.created_at,
        });
      }

      // Cancel any previous stream
      if (cancelStreamRef.current) {
        cancelStreamRef.current();
        cancelStreamRef.current = null;
      }

      const cancel = streamGeneration(
        sessionId,
        {
          query,
          format: selectedFormat,
          templateId: attachedTemplate?.id,
          sessionId,
        },
        {
          onPhaseStart: (phase) => {
            setPhase(phase);
            const phaseProgress: Record<string, number> = {
              planning: 0.1,
              designing: 0.3,
              generating: 0.5,
              validating: 0.7,
              converting: 0.8,
              qa: 0.9,
              exporting: 0.95,
            };
            setProgress(phaseProgress[phase] || 0);
          },
          onNodeStart: (data) => {
            setCurrentNode(data.node, data.description, 0);
          },
          onNodeComplete: (data) => {
            addCompletedNode({
              node: data.node,
              phase: data.phase,
              description: data.description,
              status: data.has_errors ? "error" : "completed",
              elapsedSeconds: (data as any).elapsed_seconds,
            });
            if ((data as any).progress) {
              setProgress((data as any).progress);
            }
          },
          onNodeActivity: (data) => {
            setCurrentNode(data.node, data.description, data.elapsed_seconds);
          },
          onComplete: async (data) => {
            setProgress(1);
            setPhase("done");
            setGenerating(false);
            setCurrentNode(null);

            const assistantMsg = {
              id: generateId(),
              role: "assistant" as const,
              content: t("chat.docGeneratedSuccess"),
              generationJobId: data.job_id,
              createdAt: new Date().toISOString(),
            };
            addMessage(assistantMsg);

            if (data.job_id) {
              try {
                const job = await getJobStatus(data.job_id);
                setCurrentJob(job);
                openPanel();
              } catch {
                setCurrentJob({
                  id: data.job_id,
                  status: "completed",
                  phase: "done",
                  progress: 1,
                  createdAt: new Date().toISOString(),
                });
                openPanel();
              }
            }
          },
          onError: (error) => {
            setGenerating(false);
            setPhase(null);
            setCurrentNode(null);
            addMessage({
              id: generateId(),
              role: "assistant" as const,
              content: t("chat.docGenerationError", { error }),
              createdAt: new Date().toISOString(),
            });
          },
          onMessage: (content) => {
            addMessage({
              id: generateId(),
              role: "assistant" as const,
              content,
              createdAt: new Date().toISOString(),
            });
          },
        }
      );
      cancelStreamRef.current = cancel;
    } catch (err: any) {
      setGenerating(false);
      addMessage({
        id: generateId(),
        role: "assistant" as const,
        content: t("chat.genericError", { message: err.message }),
        createdAt: new Date().toISOString(),
      });
    }
  };

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    setUploading(true);
    try {
      const template = await uploadTemplate(file);
      setAttachedTemplate(template);
    } catch {
      // silently fail
    } finally {
      setUploading(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  return (
    <div className="border-t border-border bg-background/80 backdrop-blur-sm px-3 sm:px-4 py-3 safe-area-pb">
      <div className="max-w-3xl mx-auto w-full">
        {/* Attached Template */}
        {attachedTemplate && (
          <div className="flex items-center gap-2 mb-2 px-3 py-1.5 rounded-lg bg-secondary/50 w-fit">
            <Paperclip className="w-3.5 h-3.5 text-muted-foreground" />
            <span className="text-xs text-foreground">{attachedTemplate.name}</span>
            <button
              onClick={() => setAttachedTemplate(null)}
              className="text-muted-foreground hover:text-foreground"
            >
              <X className="w-3 h-3" />
            </button>
          </div>
        )}

        {/* Input Area */}
        <div className="flex flex-col sm:flex-row sm:items-end gap-2">
          {/* Format Selector */}
          <div className="relative flex-shrink-0 self-start sm:self-auto">
            <Button
              variant="outline"
              size="sm"
              className="h-10 gap-1.5 px-2.5 sm:px-3"
              onClick={() => setShowFormats(!showFormats)}
            >
              <FormatIcon className="w-4 h-4 flex-shrink-0" />
              <span className="text-xs max-w-[5rem] sm:max-w-none truncate">
                {FORMAT_LABELS[selectedFormat]}
              </span>
              <ChevronDown className="w-3 h-3 flex-shrink-0" />
            </Button>

            {showFormats && (
              <div className="absolute bottom-12 left-0 z-50 w-40 py-1 rounded-lg border border-border bg-card shadow-xl">
                {FORMATS.map((f) => {
                  const Icon = FORMAT_ICONS[f];
                  return (
                    <button
                      key={f}
                      onClick={() => {
                        setSelectedFormat(f);
                        setShowFormats(false);
                      }}
                      className={cn(
                        "w-full flex items-center gap-2 px-3 py-2 text-sm hover:bg-accent transition-colors",
                        f === selectedFormat && "bg-accent text-primary"
                      )}
                    >
                      <Icon className="w-4 h-4" />
                      {FORMAT_LABELS[f]}
                    </button>
                  );
                })}
              </div>
            )}
          </div>

          <div className="flex flex-1 items-end gap-2 min-w-0">
            {/* Textarea */}
            <div className="flex-1 relative min-w-0">
              <textarea
                ref={textareaRef}
                value={text}
                onChange={(e) => setText(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder={t("chat.inputPlaceholder")}
                rows={1}
                className="w-full resize-none rounded-xl border border-input bg-secondary/30 px-3 sm:px-4 py-2.5 text-[15px] sm:text-sm leading-relaxed placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring min-h-[44px] max-h-[120px] scrollbar-thin"
                style={{ height: "auto" }}
                disabled={isGenerating}
              />
            </div>

            {/* Template Attach */}
            <Button
              variant="ghost"
              size="icon"
              className="h-10 w-10 flex-shrink-0"
              onClick={() => fileRef.current?.click()}
              disabled={uploading}
            >
              <Paperclip className="w-4 h-4" />
            </Button>
            <input
              ref={fileRef}
              type="file"
              accept=".pptx,.docx,.pdf"
              className="hidden"
              onChange={handleFileUpload}
            />

            {/* Send */}
            <Button
              size="icon"
              className="h-10 w-10 flex-shrink-0 bg-gradient-to-r from-indigo-500 to-purple-600 hover:from-indigo-600 hover:to-purple-700 shadow-lg shadow-indigo-500/20"
              onClick={() => handleSubmit()}
              disabled={!text.trim() || isGenerating}
            >
              <Send className="w-4 h-4 text-white" />
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
