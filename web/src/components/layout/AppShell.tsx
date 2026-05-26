"use client";

import React, { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Menu } from "lucide-react";
import { useUserStore } from "@/stores/user";
import { useDocumentStore } from "@/stores/document";
import { UserIdentifyModal } from "@/components/auth/UserIdentifyModal";
import { Sidebar } from "@/components/sidebar/Sidebar";
import { ChatPanel } from "@/components/chat/ChatPanel";
import { DocumentPanel } from "@/components/document/DocumentPanel";
import { FullscreenViewer } from "@/components/document/FullscreenViewer";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import { useSessionsLoader } from "@/hooks/useSessions";
import { useSessionStore } from "@/stores/session";

export function AppShell() {
  const { isIdentified } = useUserStore();
  const { isPanelOpen } = useDocumentStore();
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);
  const isDesktop = useMediaQuery("(min-width: 768px)");
  const isMobileSidebarOpen = !isDesktop && mobileSidebarOpen;
  const isGenerating = useSessionStore((s) => s.isGenerating);

  useSessionsLoader();

  React.useEffect(() => {
    if (!isGenerating) return;
    const warnBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warnBeforeUnload);
    return () => window.removeEventListener("beforeunload", warnBeforeUnload);
  }, [isGenerating]);

  if (!isIdentified) {
    return <UserIdentifyModal />;
  }

  const closeMobileSidebar = () => setMobileSidebarOpen(false);

  return (
    <div className="flex h-[100dvh] overflow-hidden bg-background">
      <FullscreenViewer />

      {/* Mobile backdrop */}
      <AnimatePresence>
        {isMobileSidebarOpen && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-40 bg-black/40 md:hidden"
            onClick={closeMobileSidebar}
            aria-hidden
          />
        )}
      </AnimatePresence>

      {/* Sidebar — drawer on mobile, inline on desktop */}
      <div
        className={cn(
          "flex-shrink-0 z-50",
          !isDesktop &&
            "fixed inset-y-0 left-0 transition-transform duration-200 ease-out",
          !isDesktop && !isMobileSidebarOpen && "-translate-x-full",
          isMobileSidebarOpen && "translate-x-0"
        )}
      >
        <Sidebar
          collapsed={isDesktop ? sidebarCollapsed : false}
          onToggle={() => {
            if (isDesktop) {
              setSidebarCollapsed(!sidebarCollapsed);
            } else {
              setMobileSidebarOpen(false);
            }
          }}
          isMobile={!isDesktop}
          onSessionSelect={closeMobileSidebar}
        />
      </div>

      {/* Main Chat Area */}
      <main className="flex-1 flex flex-col min-w-0 min-h-0">
        {!isDesktop && (
          <div className="flex items-center gap-2 px-3 py-2 border-b border-border flex-shrink-0 md:hidden">
            <Button
              variant="ghost"
              size="icon"
              className="h-9 w-9"
              onClick={() => setMobileSidebarOpen(true)}
              aria-label="Open menu"
            >
              <Menu className="w-5 h-5" />
            </Button>
            <span className="font-semibold text-sm tracking-tight">DocuMind</span>
          </div>
        )}
        <ChatPanel />
      </main>

      {/* Right Document Panel — overlay on mobile */}
      <AnimatePresence>
        {isPanelOpen && (
          <>
            {!isDesktop && (
              <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                className="fixed inset-0 z-40 bg-black/40 md:hidden"
                onClick={() => useDocumentStore.getState().closePanel()}
              />
            )}
            <motion.aside
              initial={
                isDesktop ? { width: 0, opacity: 0 } : { x: "100%", opacity: 0 }
              }
              animate={
                isDesktop
                  ? { width: 520, opacity: 1 }
                  : { x: 0, opacity: 1 }
              }
              exit={
                isDesktop ? { width: 0, opacity: 0 } : { x: "100%", opacity: 0 }
              }
              transition={{ duration: 0.3, ease: "easeInOut" }}
              className={cn(
                "border-l border-border overflow-hidden flex-shrink-0 bg-background z-50",
                isDesktop ? "relative h-full" : "fixed inset-y-0 right-0 w-full max-w-lg shadow-xl"
              )}
            >
              <DocumentPanel isMobile={!isDesktop} />
            </motion.aside>
          </>
        )}
      </AnimatePresence>
    </div>
  );
}
