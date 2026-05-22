import { create } from "zustand";
import type { ChatMessage, DocumentFormat, SessionSummary } from "@/types";

export interface NodeProgress {
  node: string;
  phase: string;
  description: string;
  status: "running" | "completed" | "error";
  elapsedSeconds?: number;
}

interface SessionState {
  currentSessionId: string | null;
  sessions: SessionSummary[];
  messages: ChatMessage[];
  isGenerating: boolean;
  isLoadingSession: boolean;
  currentPhase: string | null;
  progress: number;
  selectedFormat: DocumentFormat;
  currentNode: string | null;
  currentNodeDescription: string | null;
  currentNodeElapsed: number;
  completedNodes: NodeProgress[];

  setCurrentSession: (id: string | null) => void;
  setLoadingSession: (v: boolean) => void;
  setSessions: (sessions: SessionSummary[]) => void;
  addSession: (session: SessionSummary) => void;
  setMessages: (messages: ChatMessage[]) => void;
  addMessage: (message: ChatMessage) => void;
  setGenerating: (v: boolean) => void;
  setPhase: (phase: string | null) => void;
  setProgress: (progress: number) => void;
  setSelectedFormat: (format: DocumentFormat) => void;
  setCurrentNode: (node: string | null, description?: string | null, elapsed?: number) => void;
  addCompletedNode: (node: NodeProgress) => void;
  clearProgress: () => void;
  reset: () => void;
}

export const useSessionStore = create<SessionState>((set) => ({
  currentSessionId: null,
  sessions: [],
  messages: [],
  isGenerating: false,
  isLoadingSession: false,
  currentPhase: null,
  progress: 0,
  selectedFormat: "pptx",
  currentNode: null,
  currentNodeDescription: null,
  currentNodeElapsed: 0,
  completedNodes: [],

  setCurrentSession: (id) => set({ currentSessionId: id }),
  setLoadingSession: (v) => set({ isLoadingSession: v }),
  setSessions: (sessions) => set({ sessions }),
  addSession: (session) =>
    set((state) => ({ sessions: [session, ...state.sessions] })),
  setMessages: (messages) => set({ messages }),
  addMessage: (message) =>
    set((state) => ({ messages: [...state.messages, message] })),
  setGenerating: (v) => set({ isGenerating: v }),
  setPhase: (phase) => set({ currentPhase: phase }),
  setProgress: (progress) => set({ progress }),
  setSelectedFormat: (format) => set({ selectedFormat: format }),
  setCurrentNode: (node, description = null, elapsed = 0) =>
    set({ currentNode: node, currentNodeDescription: description, currentNodeElapsed: elapsed }),
  addCompletedNode: (node) =>
    set((state) => ({ completedNodes: [...state.completedNodes, node] })),
  clearProgress: () =>
    set({
      currentPhase: null,
      progress: 0,
      currentNode: null,
      currentNodeDescription: null,
      currentNodeElapsed: 0,
      completedNodes: [],
    }),
  reset: () =>
    set({
      currentSessionId: null,
      messages: [],
      isGenerating: false,
      currentPhase: null,
      progress: 0,
      currentNode: null,
      currentNodeDescription: null,
      currentNodeElapsed: 0,
      completedNodes: [],
    }),
}));
