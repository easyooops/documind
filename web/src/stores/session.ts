import { create } from "zustand";
import type { ChatMessage, DocumentFormat, SessionSummary } from "@/types";

export interface NodeProgress {
  node: string;
  phase: string;
  description: string;
  status: "running" | "completed" | "error";
  elapsedSeconds?: number;
  summaryItems?: string[];
  expanded?: boolean;
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
  addNodeActivity: (node: string, description: string, elapsed?: number, phase?: string) => void;
  toggleNodeExpanded: (node: string) => void;
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
    set((state) => {
      if (!node) {
        return { currentNode: null, currentNodeDescription: null, currentNodeElapsed: 0 };
      }
      const existing = state.completedNodes.find((item) => item.node === node);
      const nextNode: NodeProgress = {
        node,
        phase: existing?.phase ?? state.currentPhase ?? "planning",
        description: description ?? existing?.description ?? node,
        status: "running",
        elapsedSeconds: elapsed,
        summaryItems: existing?.summaryItems ?? [],
        expanded: true,
      };
      return {
        currentNode: node,
        currentNodeDescription: description,
        currentNodeElapsed: elapsed,
        completedNodes: upsertNodeProgress(state.completedNodes, nextNode),
      };
    }),
  addCompletedNode: (node) =>
    set((state) => ({
      currentNode: state.currentNode === node.node ? null : state.currentNode,
      currentNodeDescription: state.currentNode === node.node ? null : state.currentNodeDescription,
      currentNodeElapsed: state.currentNode === node.node ? 0 : state.currentNodeElapsed,
      completedNodes: upsertNodeProgress(state.completedNodes, {
        ...node,
        summaryItems: node.summaryItems ?? state.completedNodes.find((item) => item.node === node.node)?.summaryItems ?? [],
        expanded: false,
      }),
    })),
  addNodeActivity: (node, description, elapsed = 0, phase) =>
    set((state) => {
      const existing = state.completedNodes.find((item) => item.node === node);
      return {
        currentNode: node,
        currentNodeDescription: description,
        currentNodeElapsed: elapsed,
        completedNodes: upsertNodeProgress(state.completedNodes, {
          node,
          phase: phase ?? existing?.phase ?? state.currentPhase ?? "planning",
          description: existing?.description ?? description,
          status: "running",
          elapsedSeconds: elapsed,
          summaryItems: existing?.summaryItems ?? [],
          expanded: true,
        }),
      };
    }),
  toggleNodeExpanded: (node) =>
    set((state) => ({
      completedNodes: state.completedNodes.map((item) =>
        item.node === node ? { ...item, expanded: !item.expanded } : item
      ),
    })),
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

function upsertNodeProgress(nodes: NodeProgress[], next: NodeProgress): NodeProgress[] {
  const index = nodes.findIndex((item) => item.node === next.node);
  if (index === -1) return [...nodes, next];
  return nodes.map((item, i) =>
    i === index
      ? {
          ...item,
          ...next,
          summaryItems: next.summaryItems ?? item.summaryItems,
          expanded: next.expanded ?? item.expanded,
        }
      : item
  );
}
