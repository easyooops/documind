export interface User {
  id: string;
  name: string;
  email: string;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  generationJobId?: string;
  createdAt: string;
}

export interface Session {
  id: string;
  title: string | null;
  userId?: string;
  messages: ChatMessage[];
  createdAt: string;
  updatedAt: string;
}

export interface SessionSummary {
  id: string;
  title: string | null;
  lastMessage?: string;
  format?: string;
  createdAt: string;
  updatedAt: string;
}

export type DocumentFormat =
  | "pptx"
  | "docx"
  | "md"
  | "hwp"
  | "xlsx"
  | "pdf";

export type JobStatus =
  | "queued"
  | "processing"
  | "completed"
  | "failed"
  | "cancelled";

export type JobPhase =
  | "planning"
  | "designing"
  | "generating"
  | "validating"
  | "converting"
  | "qa"
  | "exporting"
  | "done";

export interface GenerationJob {
  id: string;
  status: JobStatus;
  phase: JobPhase | null;
  progress: number;
  slideCount?: number;
  fidelityScore?: number;
  error?: { message: string; type?: string };
  downloadUrl?: string;
  createdAt: string;
  completedAt?: string;
}

export interface DocumentVersion {
  id: string;
  versionNumber: number;
  trigger: string;
  userInstruction?: string;
  fidelityScore?: number;
  slideCount?: number;
  downloadUrl?: string;
  createdAt: string;
  isLatest?: boolean;
}

export interface Template {
  id: string;
  name: string;
  filename: string;
  status: string;
  sizeBytes: number;
  createdAt: string;
}

export interface ImageAttachment {
  id: string;
  filename: string;
  mimeType: string;
  sizeBytes: number;
  width?: number;
  height?: number;
  createdAt: string;
}

export interface GenerateRequest {
  query: string;
  format: DocumentFormat;
  templateId?: string;
  sessionId?: string;
  imageAttachmentIds?: string[];
  options?: Record<string, unknown>;
}

export interface SSEEvent {
  event: string;
  data: Record<string, unknown>;
  phase?: string;
  agent?: string;
}

export const FORMAT_LABELS: Record<DocumentFormat, string> = {
  pptx: "PowerPoint",
  docx: "Word",
  md: "Markdown",
  hwp: "HWPX",
  xlsx: "Excel",
  pdf: "PDF",
};

export const FORMAT_EXTENSIONS: Record<DocumentFormat, string> = {
  pptx: ".pptx",
  docx: ".docx",
  md: ".md",
  hwp: ".hwpx",
  xlsx: ".xlsx",
  pdf: ".pdf",
};

/** i18n keys under `progress.*` — use with useTranslation() */
export const JOB_PHASE_I18N_KEYS: Record<JobPhase, string> = {
  planning: "progress.planning",
  designing: "progress.designing",
  generating: "progress.generating",
  validating: "progress.validating",
  converting: "progress.converting",
  qa: "progress.qa",
  exporting: "progress.exporting",
  done: "progress.done",
};
