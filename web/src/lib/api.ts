import type {
  GenerateRequest,
  GenerationJob,
  Session,
  SessionSummary,
  Template,
  DocumentVersion,
  User,
  ChatMessage,
} from "@/types";

const API_URL =
  process.env.NEXT_PUBLIC_API_URL ?? "";

const STREAM_API_URL =
  process.env.NEXT_PUBLIC_STREAM_API_URL || process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";

interface ApiMessage {
  id?: string;
  role: ChatMessage["role"];
  content: string;
  generation_job_id?: string;
  created_at?: string;
}

interface ApiSession {
  id: string;
  title: string | null;
  messages?: ApiMessage[];
  created_at: string;
  updated_at: string;
}

interface ApiJob {
  id: string;
  status: GenerationJob["status"];
  phase: GenerationJob["phase"];
  progress?: number;
  slide_count?: number;
  fidelity_score?: number;
  error?: GenerationJob["error"];
  download_url?: string;
  created_at: string;
  completed_at?: string;
}

type StreamProgressEvent = Record<string, unknown>;

interface StreamCompleteEvent {
  job_id?: string;
}

async function request<T>(
  path: string,
  options?: RequestInit
): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    headers: { "Content-Type": "application/json", ...options?.headers },
    ...options,
  });
  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(error.detail || `API Error: ${res.status}`);
  }
  return res.json();
}

// --- Users ---

export async function identifyUser(
  name: string,
  email: string
): Promise<User> {
  return request<User>("/api/v1/users/identify", {
    method: "POST",
    body: JSON.stringify({ name, email }),
  });
}

// --- Sessions ---

export async function createSession(userId?: string): Promise<{ id: string; created_at: string }> {
  return request("/api/v1/chat/sessions", {
    method: "POST",
    body: JSON.stringify({ user_id: userId }),
  });
}

export async function getSession(sessionId: string): Promise<Session> {
  const data = await request<ApiSession>(`/api/v1/chat/sessions/${sessionId}`);
  return {
    id: data.id,
    title: data.title,
    messages: (data.messages || []).map((m) => ({
      id: m.id || crypto.randomUUID(),
      role: m.role,
      content: m.content,
      generationJobId: m.generation_job_id,
      createdAt: m.created_at || new Date().toISOString(),
    })),
    createdAt: data.created_at,
    updatedAt: data.updated_at,
  };
}

function mapSessionSummary(data: Record<string, unknown>): SessionSummary {
  return {
    id: data.id as string,
    title: (data.title as string | null) ?? null,
    lastMessage: (data.last_message as string | undefined) ?? undefined,
    format: (data.format as string | undefined) ?? undefined,
    createdAt: (data.created_at as string) || new Date().toISOString(),
    updatedAt: (data.updated_at as string) || new Date().toISOString(),
  };
}

export async function getUserSessions(userId: string): Promise<SessionSummary[]> {
  const data = await request<Record<string, unknown>[]>(
    `/api/v1/users/${userId}/sessions`
  );
  return data.map(mapSessionSummary);
}

// --- Documents ---

export async function generateDocument(
  req: GenerateRequest
): Promise<GenerationJob> {
  const data = await request<ApiJob>("/api/v1/documents/generate", {
    method: "POST",
    body: JSON.stringify({
      query: req.query,
      format: req.format,
      template_id: req.templateId,
      session_id: req.sessionId,
      options: req.options || {},
    }),
  });
  return mapJob(data);
}

export async function getJobStatus(jobId: string): Promise<GenerationJob> {
  const data = await request<ApiJob>(`/api/v1/documents/${jobId}/status`);
  return mapJob(data);
}

export async function getDocumentVersions(
  jobId: string
): Promise<DocumentVersion[]> {
  return request<DocumentVersion[]>(`/api/v1/documents/${jobId}/versions`);
}

export function getDownloadUrl(jobId: string): string {
  return `${API_URL}/api/v1/documents/${jobId}/download`;
}

export function getPreviewUrl(jobId: string): string {
  return `${API_URL}/api/v1/documents/${jobId}/preview`;
}

// --- Templates ---

export async function uploadTemplate(file: File): Promise<Template> {
  const formData = new FormData();
  formData.append("file", file);

  const res = await fetch(`${API_URL}/api/v1/templates/upload`, {
    method: "POST",
    body: formData,
  });
  if (!res.ok) {
    const error = await res.json().catch(() => ({ detail: "Template upload failed" }));
    throw new Error(error.detail || "Template upload failed");
  }
  const data = await res.json();
  return {
    id: data.id,
    name: data.name,
    filename: data.filename,
    status: data.status,
    sizeBytes: data.size_bytes ?? data.sizeBytes ?? 0,
    createdAt: data.created_at ?? data.createdAt ?? new Date().toISOString(),
  };
}

export async function listTemplates(): Promise<Template[]> {
  return request<Template[]>("/api/v1/templates/");
}

// --- SSE Streaming ---

export function streamGeneration(
  sessionId: string,
  req: GenerateRequest,
  handlers: {
    onPhaseStart?: (phase: string) => void;
    onProgress?: (data: StreamProgressEvent) => void;
    onComplete?: (data: StreamCompleteEvent) => void;
    onError?: (error: string) => void;
    onMessage?: (content: string) => void;
    onNodeStart?: (data: { node: string; phase: string; description: string }) => void;
    onNodeComplete?: (data: { node: string; phase: string; description: string; summary_items?: string[]; has_errors: boolean; progress?: number; elapsed_seconds?: number }) => void;
    onNodeActivity?: (data: { node: string; phase?: string; activity: string; description: string; elapsed_seconds: number }) => void;
  }
): () => void {
  const url = `${STREAM_API_URL}/api/v1/chat/sessions/${sessionId}/messages/stream`;

  const controller = new AbortController();

  fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      query: req.query,
      format: req.format,
      template_id: req.templateId,
      session_id: req.sessionId,
      options: req.options || {},
    }),
    signal: controller.signal,
  })
    .then(async (response) => {
      if (!response.ok) throw new Error("Stream failed");
      const reader = response.body?.getReader();
      if (!reader) return;

      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";

        let currentEvent: string | null = null;

        for (const line of lines) {
          if (line.startsWith("event: ")) {
            currentEvent = line.slice(7).trim();
          } else if (line.startsWith("data: ") && currentEvent) {
            try {
              const data = JSON.parse(line.slice(6));
              switch (currentEvent) {
                case "phase_start":
                  handlers.onPhaseStart?.(data.phase);
                  break;
                case "progress":
                  handlers.onProgress?.(data);
                  break;
                case "node_start":
                  handlers.onNodeStart?.(data);
                  break;
                case "node_complete":
                  handlers.onNodeComplete?.(data);
                  break;
                case "node_activity":
                  handlers.onNodeActivity?.(data);
                  break;
                case "complete":
                  handlers.onComplete?.(data);
                  break;
                case "error":
                  handlers.onError?.(data.message);
                  break;
                case "message":
                  handlers.onMessage?.(data.content);
                  break;
              }
            } catch {
              // skip malformed JSON
            }
            currentEvent = null;
          } else if (line.trim() === "") {
            currentEvent = null;
          }
        }
      }
    })
    .catch((err) => {
      if (err.name !== "AbortError") {
        handlers.onError?.(err.message);
      }
    });

  return () => controller.abort();
}

// --- Helpers ---

function mapJob(data: ApiJob): GenerationJob {
  return {
    id: data.id,
    status: data.status,
    phase: data.phase,
    progress: data.progress || 0,
    slideCount: data.slide_count,
    fidelityScore: data.fidelity_score,
    error: data.error,
    downloadUrl: data.download_url,
    createdAt: data.created_at,
    completedAt: data.completed_at,
  };
}
