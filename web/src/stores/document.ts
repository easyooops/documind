import { create } from "zustand";
import type { DocumentVersion, GenerationJob } from "@/types";

interface DocumentState {
  currentJob: GenerationJob | null;
  versions: DocumentVersion[];
  isPanelOpen: boolean;
  isFullscreen: boolean;

  setCurrentJob: (job: GenerationJob | null) => void;
  setVersions: (versions: DocumentVersion[]) => void;
  openPanel: () => void;
  closePanel: () => void;
  togglePanel: () => void;
  setFullscreen: (v: boolean) => void;
  reset: () => void;
}

export const useDocumentStore = create<DocumentState>((set) => ({
  currentJob: null,
  versions: [],
  isPanelOpen: false,
  isFullscreen: false,

  setCurrentJob: (job) => set({ currentJob: job }),
  setVersions: (versions) => set({ versions }),
  openPanel: () => set({ isPanelOpen: true }),
  closePanel: () => set({ isPanelOpen: false }),
  togglePanel: () => set((s) => ({ isPanelOpen: !s.isPanelOpen })),
  setFullscreen: (v) => set({ isFullscreen: v }),
  reset: () =>
    set({
      currentJob: null,
      versions: [],
      isPanelOpen: false,
      isFullscreen: false,
    }),
}));
