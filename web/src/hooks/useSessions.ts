"use client";

import { useEffect, useCallback } from "react";
import { getUserSessions, getSession } from "@/lib/api";
import { useUserStore } from "@/stores/user";
import { useSessionStore } from "@/stores/session";
import { useDocumentStore } from "@/stores/document";
import { getDocumentVersions, getJobStatus } from "@/lib/api";
import type { DocumentFormat } from "@/types";

export function useSessionsLoader() {
  const user = useUserStore((s) => s.user);
  const setSessions = useSessionStore((s) => s.setSessions);

  useEffect(() => {
    if (!user?.id) return;

    let cancelled = false;
    getUserSessions(user.id)
      .then((sessions) => {
        if (!cancelled) setSessions(sessions);
      })
      .catch(() => {
        /* keep local sessions on failure */
      });

    return () => {
      cancelled = true;
    };
  }, [user?.id, setSessions]);
}

export function useSessionSelect(onAfterSelect?: () => void) {
  const setCurrentSession = useSessionStore((s) => s.setCurrentSession);
  const setMessages = useSessionStore((s) => s.setMessages);
  const setLoadingSession = useSessionStore((s) => s.setLoadingSession);
  const clearProgress = useSessionStore((s) => s.clearProgress);
  const setSelectedFormat = useSessionStore((s) => s.setSelectedFormat);

  const selectSession = useCallback(
    async (sessionId: string) => {
      const { currentSessionId } = useSessionStore.getState();
      if (sessionId === currentSessionId) {
        onAfterSelect?.();
        return;
      }

      setLoadingSession(true);
      clearProgress();
      setCurrentSession(sessionId);
      useDocumentStore.getState().reset();

      try {
        const session = await getSession(sessionId);
        setMessages(session.messages);
        const storedFormat = useSessionStore.getState().sessions.find(
          (item) => item.id === sessionId
        )?.format;
        if (storedFormat && ["pptx", "docx", "pdf", "md", "xlsx", "hwp"].includes(storedFormat)) {
          setSelectedFormat(storedFormat as DocumentFormat);
        }

        const lastJobMsg = [...session.messages]
          .reverse()
          .find((m) => m.generationJobId);
        if (lastJobMsg?.generationJobId) {
          const job = await getJobStatus(lastJobMsg.generationJobId);
          if (job.status === "completed") {
            useDocumentStore.getState().setCurrentJob(job);
            const versions = await getDocumentVersions(lastJobMsg.generationJobId);
            useDocumentStore.getState().setVersions(versions);
          }
        }
      } catch {
        setMessages([]);
      } finally {
        setLoadingSession(false);
        onAfterSelect?.();
      }
    },
    [setCurrentSession, setMessages, setLoadingSession, clearProgress, setSelectedFormat, onAfterSelect]
  );

  return selectSession;
}
