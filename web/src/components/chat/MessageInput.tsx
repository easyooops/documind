"use client";

import React, { useEffect, useState, useRef } from "react";
import {
  Send,
  Paperclip,
  ChevronDown,
  FileText,
  Presentation,
  FileSpreadsheet,
  Code2,
  X,
  AlertCircle,
  ImagePlus,
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
  getDocumentVersions,
  uploadChatImage,
} from "@/lib/api";
import {
  FORMAT_LABELS,
  type DocumentFormat,
  type ImageAttachment,
  type Template,
} from "@/types";
import { generateId } from "@/lib/utils";
import { useTranslation } from "@/hooks/useTranslation";

const FORMAT_ICONS: Record<DocumentFormat, React.ElementType> = {
  pptx: Presentation,
  docx: FileText,
  md: Code2,
  hwp: FileText,
  xlsx: FileSpreadsheet,
  pdf: FileText,
};

const FORMATS: DocumentFormat[] = ["pptx", "docx", "pdf", "md", "xlsx"];
const TEMPLATE_EXTENSIONS: Record<DocumentFormat, string[]> = {
  pptx: ["pptx", "potx"],
  docx: ["docx"],
  pdf: ["pdf"],
  md: ["md"],
  xlsx: ["xlsx"],
  hwp: ["hwpx"],
};
const MAX_IMAGES = 4;

interface PendingImage extends ImageAttachment {
  previewUrl: string;
}

export function MessageInput() {
  const [text, setText] = useState("");
  const [showFormats, setShowFormats] = useState(false);
  const [attachedTemplate, setAttachedTemplate] = useState<Template | null>(null);
  const [uploading, setUploading] = useState(false);
  const [attachedImages, setAttachedImages] = useState<PendingImage[]>([]);
  const [uploadingImage, setUploadingImage] = useState(false);
  const [isDraggingImage, setIsDraggingImage] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const imageRef = useRef<HTMLInputElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const cancelStreamRef = useRef<(() => void) | null>(null);
  const attachedImagesRef = useRef<PendingImage[]>([]);

  const { user } = useUserStore();
  const {
    currentSessionId,
    messages,
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
  const {
    versions,
    selectedVersionNumber,
    setVersions,
    selectVersion,
    setCurrentJob,
    openPanel,
  } = useDocumentStore();
  const { t, locale } = useTranslation();

  const FormatIcon = FORMAT_ICONS[selectedFormat];
  const isFirstTurn = !currentSessionId && messages.length === 0 && !isGenerating;
  const canAttachTemplate = isFirstTurn;
  const formatLocked = versions.length > 0 || messages.some((message) => Boolean(message.generationJobId));
  const templateExtensions = TEMPLATE_EXTENSIONS[selectedFormat];
  const templateButtonLabel = t("chat.templateButton", { defaultValue: "Template" });
  const templateHelpText = t("chat.templateHelp", {
    defaultValue: "A native-format template can be attached only before the first message.",
  });

  useEffect(() => {
    attachedImagesRef.current = attachedImages;
  }, [attachedImages]);

  useEffect(() => () => {
    attachedImagesRef.current.forEach((image) => URL.revokeObjectURL(image.previewUrl));
  }, []);

  const handleSubmit = async (e?: React.FormEvent) => {
    e?.preventDefault();
    if ((!text.trim() && attachedImages.length === 0) || isGenerating || uploadingImage) return;

    const query = text.trim() || t("chat.imageOnlyPrompt", {
      defaultValue: "첨부 이미지를 분석하여 문서에 필요한 변경을 반영해주세요.",
    });
    const submittedImages = attachedImages;
    setText("");
    setShowFormats(false);
    clearProgress();
    setGenerating(true);
    setPhase("planning");
    setProgress(0);

    const userMessage = {
      id: generateId(),
      role: "user" as const,
      content: `${query}${submittedImages.length ? `\n[${submittedImages.length} image(s) attached]` : ""}`,
      createdAt: new Date().toISOString(),
    };
    addMessage(userMessage);

    try {
      let sessionId = currentSessionId;
      const templateForFirstTurn = canAttachTemplate ? attachedTemplate : null;
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
          templateId: templateForFirstTurn?.id,
          sessionId,
          imageAttachmentIds: submittedImages.map((image) => image.id),
          options: {
            locale,
            ...(selectedVersionNumber ? { base_version_number: selectedVersionNumber } : {}),
          },
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
              elapsedSeconds: data.elapsed_seconds,
              summaryItems: data.summary_items ?? [],
            });
            if (data.progress) {
              setProgress(data.progress);
            }
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
                const documentVersions = await getDocumentVersions(data.job_id);
                setVersions(documentVersions);
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
      setAttachedTemplate(null);
      submittedImages.forEach((image) => URL.revokeObjectURL(image.previewUrl));
      setAttachedImages([]);
    } catch (err) {
      setGenerating(false);
      const message = err instanceof Error ? err.message : String(err);
      addMessage({
        id: generateId(),
        role: "assistant" as const,
        content: t("chat.genericError", { message }),
        createdAt: new Date().toISOString(),
      });
    }
  };

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploadError(null);

    const extension = file.name.split(".").pop()?.toLowerCase();
    if (!canAttachTemplate || !templateExtensions.includes(extension || "")) {
      setUploadError(
        t("chat.templateAttachHint", {
          defaultValue: `Attach a ${templateExtensions.map((item) => `.${item}`).join(" or ")} template before the first message.`,
        })
      );
      e.target.value = "";
      return;
    }

    setUploading(true);
    try {
      const template = await uploadTemplate(file);
      setAttachedTemplate(template);
    } catch (err) {
      const message = err instanceof Error ? err.message : "Template upload failed";
      setUploadError(message);
    } finally {
      setUploading(false);
      e.target.value = "";
    }
  };

  const addImages = async (files: File[]) => {
    if (isGenerating) return;
    const remaining = MAX_IMAGES - attachedImages.length;
    const selectedFiles = files.filter((file) => file.type.startsWith("image/")).slice(0, remaining);
    if (!selectedFiles.length) {
      setUploadError(t("chat.imageAttachHint", {
        defaultValue: "PNG, JPEG, WEBP, or GIF images can be attached.",
      }));
      return;
    }
    setUploadError(null);
    setUploadingImage(true);
    try {
      const uploaded: PendingImage[] = [];
      for (const file of selectedFiles) {
        const attachment = await uploadChatImage(file);
        uploaded.push({ ...attachment, previewUrl: URL.createObjectURL(file) });
      }
      setAttachedImages((current) => [...current, ...uploaded].slice(0, MAX_IMAGES));
    } catch (err) {
      const message = err instanceof Error ? err.message : "Image upload failed";
      setUploadError(message);
    } finally {
      setUploadingImage(false);
    }
  };

  const handleImageInput = async (e: React.ChangeEvent<HTMLInputElement>) => {
    await addImages(Array.from(e.target.files || []));
    e.target.value = "";
  };

  const removeImage = (id: string) => {
    setAttachedImages((current) => {
      const removed = current.find((image) => image.id === id);
      if (removed) URL.revokeObjectURL(removed.previewUrl);
      return current.filter((image) => image.id !== id);
    });
  };

  const handleImageDrop = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setIsDraggingImage(false);
    void addImages(Array.from(e.dataTransfer.files));
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  return (
    <div
      className={cn(
        "border-t border-border bg-background/80 backdrop-blur-sm px-3 sm:px-4 py-3 safe-area-pb transition-colors",
        isDraggingImage && "bg-primary/10 ring-2 ring-inset ring-primary/40",
      )}
      onDragOver={(event) => {
        event.preventDefault();
        setIsDraggingImage(true);
      }}
      onDragLeave={() => setIsDraggingImage(false)}
      onDrop={handleImageDrop}
    >
      <div className="max-w-3xl mx-auto w-full">
        {/* Attached Template */}
        {attachedTemplate && (
          <div className="flex items-center gap-2 mb-2 px-3 py-2 rounded-lg border border-primary/20 bg-primary/5 w-fit max-w-full">
            <Presentation className="w-4 h-4 text-primary flex-shrink-0" />
            <span className="text-xs font-medium text-primary flex-shrink-0">
              {t("chat.templateBasis", { defaultValue: "Template basis" })}
            </span>
            <span className="text-xs text-foreground truncate max-w-[14rem] sm:max-w-[24rem]">
              {attachedTemplate.filename}
            </span>
            <button
              onClick={() => setAttachedTemplate(null)}
              className="text-muted-foreground hover:text-foreground disabled:opacity-40"
              disabled={!canAttachTemplate}
              title={canAttachTemplate ? undefined : templateHelpText}
            >
              <X className="w-3 h-3" />
            </button>
          </div>
        )}
        {attachedImages.length > 0 && (
          <div className="flex gap-2 mb-2 flex-wrap">
            {attachedImages.map((image) => (
              <div key={image.id} className="relative w-20 h-16 rounded-lg border border-border overflow-hidden bg-secondary">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={image.previewUrl} alt={image.filename} className="w-full h-full object-cover" />
                <button
                  type="button"
                  onClick={() => removeImage(image.id)}
                  className="absolute right-1 top-1 rounded-full bg-background/80 p-0.5 text-foreground"
                  aria-label={t("chat.removeImage", { defaultValue: "Remove image" })}
                >
                  <X className="w-3 h-3" />
                </button>
              </div>
            ))}
          </div>
        )}
        {uploadError && (
          <div className="flex items-center gap-1.5 mb-2 text-xs text-destructive">
            <AlertCircle className="w-3.5 h-3.5" />
            <span>{uploadError}</span>
          </div>
        )}

        {/* Input Area */}
        <div className="flex flex-col sm:flex-row sm:items-center gap-2">
          {/* Format Selector */}
          <div className="relative flex-shrink-0 self-start sm:self-auto">
            <Button
              variant="outline"
              size="sm"
              className="h-11 gap-1.5 px-2.5 sm:px-3"
              onClick={() => setShowFormats(!showFormats)}
              disabled={formatLocked || isGenerating}
              title={formatLocked ? t("chat.formatLocked", {
                defaultValue: "The output format is fixed after the first document is generated.",
              }) : undefined}
            >
              <FormatIcon className="w-4 h-4 flex-shrink-0" />
              <span className="text-xs max-w-[5rem] sm:max-w-none truncate">
                {FORMAT_LABELS[selectedFormat]}
              </span>
              <ChevronDown className="w-3 h-3 flex-shrink-0" />
            </Button>

            {showFormats && !formatLocked && !isGenerating && (
              <div className="absolute bottom-12 left-0 z-50 w-40 py-1 rounded-lg border border-border bg-card shadow-xl">
                {FORMATS.map((f) => {
                  const Icon = FORMAT_ICONS[f];
                  return (
                    <button
                      key={f}
                      onClick={() => {
                        setSelectedFormat(f);
                        if (f !== selectedFormat) {
                          setAttachedTemplate(null);
                          setUploadError(null);
                        }
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

          <div className="flex flex-1 items-center gap-2 min-w-0">
            {/* Textarea */}
            <div className="flex-1 relative min-w-0">
              <textarea
                ref={textareaRef}
                value={text}
                onChange={(e) => setText(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder={t("chat.inputPlaceholder")}
                rows={1}
                className="block w-full h-11 resize-none rounded-xl border border-input bg-secondary/30 px-3 sm:px-4 py-2.5 text-[15px] sm:text-sm leading-6 placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring max-h-[120px] scrollbar-thin"
                disabled={isGenerating}
              />
            </div>

            {/* Initial Template Attach / Existing Document Version */}
            {canAttachTemplate ? (
              <>
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-11 flex-shrink-0 gap-1.5 px-3 border border-dashed border-primary/30 bg-primary/5 text-primary hover:bg-primary/10"
                  onClick={() => fileRef.current?.click()}
                  disabled={uploading}
                  title={templateHelpText}
                >
                  <Paperclip className="w-4 h-4" />
                  <span className="hidden sm:inline text-xs">{uploading ? "..." : templateButtonLabel}</span>
                </Button>
                <input
                  ref={fileRef}
                  type="file"
                  accept={templateExtensions.map((item) => `.${item}`).join(",")}
                  className="hidden"
                  onChange={handleFileUpload}
                />
              </>
            ) : selectedFormat === "pptx" && versions.length > 0 ? (
              <select
                value={selectedVersionNumber ?? versions[0].versionNumber}
                onChange={(event) => selectVersion(Number(event.target.value))}
                className="h-11 rounded-lg border border-input bg-background px-2.5 text-xs text-foreground"
                disabled={isGenerating}
                title={t("document.versionHistory")}
              >
                {versions.map((version) => (
                  <option key={version.id} value={version.versionNumber}>
                    v{version.versionNumber}{version.isLatest ? " (latest)" : ""}
                  </option>
                ))}
              </select>
            ) : null}

            <Button
              variant="ghost"
              size="sm"
              className="h-11 flex-shrink-0 gap-1.5 px-3"
              onClick={() => imageRef.current?.click()}
              disabled={isGenerating || uploadingImage || attachedImages.length >= MAX_IMAGES}
              title={t("chat.imageHelp", { defaultValue: "Drop or attach reference images" })}
            >
              <ImagePlus className="w-4 h-4" />
              <span className="hidden sm:inline text-xs">{uploadingImage ? "..." : t("chat.imageButton", { defaultValue: "Image" })}</span>
            </Button>
            <input
              ref={imageRef}
              type="file"
              accept="image/png,image/jpeg,image/webp,image/gif"
              multiple
              className="hidden"
              onChange={handleImageInput}
            />

            {/* Send */}
            <Button
              size="icon"
              className="h-11 w-11 flex-shrink-0 bg-gradient-to-r from-indigo-500 to-purple-600 hover:from-indigo-600 hover:to-purple-700 shadow-lg shadow-indigo-500/20"
              onClick={() => handleSubmit()}
              disabled={(!text.trim() && attachedImages.length === 0) || isGenerating || uploadingImage}
            >
              <Send className="w-4 h-4 text-white" />
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
