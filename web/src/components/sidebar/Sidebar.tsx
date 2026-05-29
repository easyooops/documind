"use client";

import React from "react";
import { motion } from "framer-motion";
import {
  Plus,
  MessageSquare,
  PanelLeftClose,
  PanelLeftOpen,
  FileText,
  LogOut,
  Loader2,
  Presentation,
  FileSpreadsheet,
  Code2,
  Pencil,
  Trash2,
  Check,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useSessionStore } from "@/stores/session";
import { useUserStore } from "@/stores/user";
import { useDocumentStore } from "@/stores/document";
import { useLocaleStore } from "@/stores/locale";
import { cn, formatDate } from "@/lib/utils";
import { useTranslation } from "@/hooks/useTranslation";
import { LanguageSwitcher } from "@/components/layout/LanguageSwitcher";
import { useSessionSelect } from "@/hooks/useSessions";
import { deleteSession, updateSessionTitle } from "@/lib/api";
import type { SessionSummary } from "@/types";

const FORMAT_ICONS: Record<string, React.ElementType> = {
  pptx: Presentation,
  docx: FileText,
  md: Code2,
  hwp: FileText,
  xlsx: FileSpreadsheet,
  pdf: FileText,
};

const FORMAT_COLORS: Record<string, string> = {
  pptx: "text-orange-500",
  docx: "text-blue-500",
  md: "text-gray-500",
  hwp: "text-sky-500",
  xlsx: "text-emerald-500",
  pdf: "text-red-500",
};

interface SidebarProps {
  collapsed: boolean;
  onToggle: () => void;
  isMobile?: boolean;
  onSessionSelect?: () => void;
}

export function Sidebar({
  collapsed,
  onToggle,
  isMobile = false,
  onSessionSelect,
}: SidebarProps) {
  const {
    sessions,
    currentSessionId,
    isLoadingSession,
    isGenerating,
    reset,
    updateSession,
    removeSession,
  } = useSessionStore();
  const { user, clearUser } = useUserStore();
  const documentStore = useDocumentStore();
  const locale = useLocaleStore((s) => s.locale);
  const { t } = useTranslation();
  const selectSession = useSessionSelect(onSessionSelect);
  const [pendingNavigation, setPendingNavigation] = React.useState<
    (() => void | Promise<void>) | null
  >(null);
  const [editingSessionId, setEditingSessionId] = React.useState<string | null>(null);
  const [editingTitle, setEditingTitle] = React.useState("");
  const [deletingSession, setDeletingSession] = React.useState<SessionSummary | null>(null);
  const [isSessionMutating, setIsSessionMutating] = React.useState(false);

  const requestNavigation = (action: () => void | Promise<void>) => {
    if (isGenerating) {
      setPendingNavigation(() => action);
      return;
    }
    void action();
  };

  const handleNewChat = () => requestNavigation(() => {
    reset();
    documentStore.reset();
    onSessionSelect?.();
  });

  const handleSessionSelect = (sessionId: string) => {
    if (sessionId === currentSessionId) {
      onSessionSelect?.();
      return;
    }
    requestNavigation(() => selectSession(sessionId));
  };

  const startEditingSession = (
    event: React.MouseEvent,
    session: SessionSummary
  ) => {
    event.stopPropagation();
    setEditingSessionId(session.id);
    setEditingTitle(session.title || t("sidebar.newChat"));
  };

  const cancelEditingSession = (event?: React.MouseEvent) => {
    event?.stopPropagation();
    setEditingSessionId(null);
    setEditingTitle("");
  };

  const submitSessionTitle = async (event?: React.FormEvent | React.MouseEvent) => {
    event?.preventDefault();
    event?.stopPropagation();
    if (!editingSessionId) return;
    const title = editingTitle.trim();
    if (!title) return;

    setIsSessionMutating(true);
    try {
      const updated = await updateSessionTitle(editingSessionId, title);
      updateSession(updated);
      cancelEditingSession();
    } finally {
      setIsSessionMutating(false);
    }
  };

  const requestDeleteSession = (
    event: React.MouseEvent,
    session: SessionSummary
  ) => {
    event.stopPropagation();
    setDeletingSession(session);
  };

  const confirmDeleteSession = async () => {
    if (!deletingSession) return;
    const sessionId = deletingSession.id;
    setIsSessionMutating(true);
    try {
      await deleteSession(sessionId);
      removeSession(sessionId);
      if (sessionId === currentSessionId) {
        documentStore.reset();
      }
      setDeletingSession(null);
    } finally {
      setIsSessionMutating(false);
    }
  };

  const continueNavigation = () => {
    const action = pendingNavigation;
    setPendingNavigation(null);
    if (action) void action();
  };

  const sidebarWidth = collapsed && !isMobile ? 60 : 280;

  return (
    <motion.aside
      animate={{ width: sidebarWidth }}
      transition={{ duration: 0.2, ease: "easeInOut" }}
      className={cn(
        "flex flex-col h-full border-r border-border bg-card/50 flex-shrink-0 overflow-hidden",
        isMobile && "w-[min(280px,85vw)] shadow-xl"
      )}
      style={isMobile ? { width: "min(280px, 85vw)" } : undefined}
    >
      {/* Header */}
      <div className="flex items-center justify-between p-3 border-b border-border min-w-0">
        {(!collapsed || isMobile) && (
          <div className="flex items-center gap-2 min-w-0">
            <div className="w-7 h-7 rounded-lg bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center flex-shrink-0">
              <FileText className="w-4 h-4 text-white" />
            </div>
            <span className="font-semibold text-sm tracking-tight truncate">
              DocuMind
            </span>
          </div>
        )}
        <Button
          variant="ghost"
          size="icon"
          onClick={onToggle}
          className="h-8 w-8 flex-shrink-0"
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        >
          {collapsed && !isMobile ? (
            <PanelLeftOpen className="w-4 h-4" />
          ) : (
            <PanelLeftClose className="w-4 h-4" />
          )}
        </Button>
      </div>

      {/* Language */}
      <div
        className={cn(
          "px-3 pb-2 min-w-0",
          collapsed && !isMobile && "flex justify-center"
        )}
      >
        <LanguageSwitcher collapsed={collapsed && !isMobile} />
      </div>

      {/* New Chat Button */}
      <div className="px-3 pt-0 pb-2 min-w-0">
        <Button
          onClick={handleNewChat}
          variant="outline"
          className={cn(
            "w-full justify-start gap-2 border-dashed",
            collapsed && !isMobile && "justify-center px-0"
          )}
        >
          <Plus className="w-4 h-4 flex-shrink-0" />
          {(!collapsed || isMobile) && (
            <span className="truncate">{t("sidebar.newDocument")}</span>
          )}
        </Button>
      </div>

      {/* Session List */}
      <ScrollArea className="flex-1 min-h-0 min-w-0">
        <div className="px-2 pb-4 space-y-0.5 min-w-0">
          {sessions.length === 0 && (!collapsed || isMobile) && (
            <p className="px-3 py-6 text-xs text-muted-foreground text-center">
              {t("sidebar.noHistory")}
            </p>
          )}
          {sessions.map((session) => {
            const Icon = session.format
              ? FORMAT_ICONS[session.format] || MessageSquare
              : MessageSquare;
            const color = session.format ? FORMAT_COLORS[session.format] || "" : "";
            const isEditing = editingSessionId === session.id;
            const isSelected = currentSessionId === session.id;

            return (
              <div
                key={session.id}
                className={cn(
                  "group w-full min-w-0 rounded-lg text-sm transition-colors duration-150",
                  "hover:bg-accent",
                  collapsed && !isMobile ? "flex justify-center" : "flex items-stretch",
                  isSelected
                    ? "bg-primary/10 text-foreground ring-1 ring-primary/20"
                    : "text-muted-foreground"
                )}
              >
                {collapsed && !isMobile ? (
                  <button
                    type="button"
                    onClick={() => handleSessionSelect(session.id)}
                    disabled={isLoadingSession}
                    className="w-full px-2 py-2.5"
                    title={session.title || t("sidebar.newChat")}
                  >
                    <Icon className={cn("w-4 h-4 mx-auto flex-shrink-0", color)} />
                  </button>
                ) : isEditing ? (
                  <form
                    className="flex min-w-0 flex-1 items-center gap-1 px-2 py-2"
                    onSubmit={submitSessionTitle}
                  >
                    <Input
                      autoFocus
                      value={editingTitle}
                      onChange={(event) => setEditingTitle(event.target.value)}
                      className="h-8 min-w-0 text-xs"
                      maxLength={120}
                      disabled={isSessionMutating}
                      aria-label={t("sidebar.renameSession")}
                    />
                    <Button
                      type="submit"
                      variant="ghost"
                      size="icon"
                      className="h-7 w-7 flex-shrink-0"
                      disabled={isSessionMutating || !editingTitle.trim()}
                      aria-label={t("sidebar.saveSessionTitle")}
                    >
                      <Check className="w-3.5 h-3.5" />
                    </Button>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      className="h-7 w-7 flex-shrink-0"
                      onClick={cancelEditingSession}
                      disabled={isSessionMutating}
                      aria-label={t("sidebar.cancelSessionTitle")}
                    >
                      <X className="w-3.5 h-3.5" />
                    </Button>
                  </form>
                ) : (
                  <>
                    <button
                      type="button"
                      onClick={() => handleSessionSelect(session.id)}
                      disabled={isLoadingSession}
                      className="min-w-0 flex-1 px-3 py-2.5 text-left"
                    >
                      <div className="flex items-start gap-2 min-w-0 overflow-hidden">
                        <Icon className={cn("w-4 h-4 mt-0.5 flex-shrink-0", color)} />
                        <div className="flex flex-col gap-0.5 min-w-0 overflow-hidden flex-1">
                          <span className="font-medium text-foreground line-clamp-2 leading-snug break-words">
                            {session.title || t("sidebar.newChat")}
                          </span>
                          <div className="flex items-center gap-1.5">
                            {session.format && (
                              <span className="text-[10px] px-1 py-0.5 rounded bg-secondary font-medium uppercase">
                                {session.format}
                              </span>
                            )}
                            <span className="text-xs text-muted-foreground truncate">
                              {formatDate(session.createdAt || session.updatedAt, locale)}
                            </span>
                          </div>
                        </div>
                      </div>
                    </button>
                    <div
                      className={cn(
                        "flex flex-shrink-0 items-center pr-1 transition-opacity",
                        isMobile
                          ? "opacity-100"
                          : "opacity-0 group-hover:opacity-100 group-focus-within:opacity-100"
                      )}
                    >
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        className="h-7 w-7"
                        onClick={(event) => startEditingSession(event, session)}
                        disabled={isSessionMutating}
                        aria-label={t("sidebar.renameSession")}
                      >
                        <Pencil className="w-3.5 h-3.5" />
                      </Button>
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        className="h-7 w-7 text-destructive hover:text-destructive"
                        onClick={(event) => requestDeleteSession(event, session)}
                        disabled={isSessionMutating}
                        aria-label={t("sidebar.deleteSession")}
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </Button>
                    </div>
                  </>
                )}
              </div>
            );
          })}
          {isLoadingSession && (!collapsed || isMobile) && (
            <div className="flex items-center justify-center gap-2 py-3 text-xs text-muted-foreground">
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
              {t("sidebar.loading")}
            </div>
          )}
        </div>
      </ScrollArea>

      {/* User Info */}
      {(!collapsed || isMobile) && user && (
        <div className="p-3 border-t border-border min-w-0">
          <div className="flex items-center justify-between gap-2 min-w-0">
            <div className="flex items-center gap-2 min-w-0 flex-1">
              <div className="w-7 h-7 rounded-full bg-gradient-to-br from-indigo-400 to-purple-500 flex items-center justify-center flex-shrink-0">
                <span className="text-xs font-medium text-white">
                  {user.name.charAt(0).toUpperCase()}
                </span>
              </div>
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium truncate leading-tight">
                  {user.name}
                </p>
                <p className="text-xs text-muted-foreground truncate leading-tight">
                  {user.email}
                </p>
              </div>
            </div>
            <Button
              variant="ghost"
              size="icon"
              className="h-7 w-7 flex-shrink-0"
              onClick={() => requestNavigation(clearUser)}
              aria-label="Log out"
            >
              <LogOut className="w-3.5 h-3.5" />
            </Button>
          </div>
        </div>
      )}
      <Dialog
        open={pendingNavigation !== null}
        onOpenChange={(open) => {
          if (!open) setPendingNavigation(null);
        }}
      >
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>{t("navigation.processingTitle")}</DialogTitle>
            <DialogDescription>{t("navigation.processingDescription")}</DialogDescription>
          </DialogHeader>
          <div className="flex justify-end gap-2 pt-2">
            <Button variant="outline" onClick={() => setPendingNavigation(null)}>
              {t("navigation.stay")}
            </Button>
            <Button onClick={continueNavigation}>{t("navigation.leave")}</Button>
          </div>
        </DialogContent>
      </Dialog>
      <Dialog
        open={deletingSession !== null}
        onOpenChange={(open) => {
          if (!open && !isSessionMutating) setDeletingSession(null);
        }}
      >
        <DialogContent className="max-w-sm">
          <DialogHeader>
            <DialogTitle>{t("sidebar.deleteSessionTitle")}</DialogTitle>
            <DialogDescription>
              {t("sidebar.deleteSessionDescription")}
            </DialogDescription>
          </DialogHeader>
          <div className="flex justify-end gap-2 pt-2">
            <Button
              variant="outline"
              onClick={() => setDeletingSession(null)}
              disabled={isSessionMutating}
            >
              {t("sidebar.cancelDeleteSession")}
            </Button>
            <Button
              variant="destructive"
              onClick={confirmDeleteSession}
              disabled={isSessionMutating}
            >
              {isSessionMutating ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                t("sidebar.confirmDeleteSession")
              )}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </motion.aside>
  );
}
