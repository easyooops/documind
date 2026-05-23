"use client";

import React, { useRef, useEffect } from "react";
import { motion } from "framer-motion";
import { User, Bot, FileDown } from "lucide-react";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Button } from "@/components/ui/button";
import { useSessionStore } from "@/stores/session";
import { useDocumentStore } from "@/stores/document";
import { cn } from "@/lib/utils";
import { getDownloadUrl } from "@/lib/api";
import { useTranslation } from "@/hooks/useTranslation";
import { ProgressIndicator } from "./ProgressIndicator";

export function MessageList() {
  const { messages, isGenerating, currentPhase, progress, completedNodes } = useSessionStore();
  const { currentJob, openPanel } = useDocumentStore();
  const { t } = useTranslation();
  const bottomRef = useRef<HTMLDivElement>(null);

  const showProgress = isGenerating || (currentPhase === "done" && completedNodes.length > 0);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isGenerating, currentPhase, completedNodes.length]);

  return (
    <ScrollArea className="flex-1 min-h-0 px-3 sm:px-4">
      <div className="max-w-3xl mx-auto py-4 sm:py-6 space-y-5 sm:space-y-6">
        {messages.map((msg, i) => (
          <motion.div
            key={msg.id || i}
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.2 }}
            className={cn(
              "flex gap-3",
              msg.role === "user" && "justify-end"
            )}
          >
            {msg.role !== "user" && (
              <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center flex-shrink-0 mt-0.5">
                <Bot className="w-4 h-4 text-white" />
              </div>
            )}

            <div
              className={cn(
                "max-w-[min(85%,32rem)] rounded-2xl px-3.5 sm:px-4 py-2.5 sm:py-3 text-[15px] sm:text-sm leading-relaxed",
                msg.role === "user"
                  ? "bg-primary text-primary-foreground rounded-br-md"
                  : "glass-card rounded-bl-md"
              )}
            >
              <p className="whitespace-pre-wrap">{msg.content}</p>

              {msg.generationJobId && currentJob?.status === "completed" && (
                <div className="mt-3 flex gap-2">
                  <Button
                    size="sm"
                    variant="secondary"
                    className="h-7 text-xs"
                    onClick={openPanel}
                  >
                    {t("chat.viewDocument")}
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    className="h-7 text-xs"
                    asChild
                  >
                    <a
                      href={getDownloadUrl(msg.generationJobId)}
                      download
                    >
                      <FileDown className="w-3 h-3 mr-1" />
                      {t("chat.download")}
                    </a>
                  </Button>
                </div>
              )}
            </div>

            {msg.role === "user" && (
              <div className="w-8 h-8 rounded-lg bg-secondary flex items-center justify-center flex-shrink-0 mt-0.5">
                <User className="w-4 h-4 text-muted-foreground" />
              </div>
            )}
          </motion.div>
        ))}

        {showProgress && (
          <ProgressIndicator phase={currentPhase} progress={progress} />
        )}

        <div ref={bottomRef} />
      </div>
    </ScrollArea>
  );
}
