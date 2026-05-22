"use client";

import React from "react";
import { useSessionStore } from "@/stores/session";
import { WelcomeGuide } from "./WelcomeGuide";
import { MessageList } from "./MessageList";
import { MessageInput } from "./MessageInput";

export function ChatPanel() {
  const { messages, currentSessionId, isLoadingSession } = useSessionStore();
  const isEmpty =
    messages.length === 0 && !currentSessionId && !isLoadingSession;

  return (
    <div className="flex flex-col flex-1 min-h-0">
      {isEmpty ? <WelcomeGuide /> : <MessageList />}
      <MessageInput />
    </div>
  );
}
