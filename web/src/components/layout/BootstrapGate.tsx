"use client";

import React from "react";
import { CheckCircle2, Database, Loader2, ServerCog } from "lucide-react";
import { getBootstrapStatus, type BootstrapStatus } from "@/lib/api";

interface BootstrapGateProps {
  children: React.ReactNode;
}

const initialStatus: BootstrapStatus = {
  ready: false,
  phase: "starting",
  message: "Preparing server configuration.",
  total: 0,
  completed: 0,
  created: 0,
  skipped: 0,
  failed: 0,
  progress: 0,
  error: null,
};

export function BootstrapGate({ children }: BootstrapGateProps) {
  const [status, setStatus] = React.useState<BootstrapStatus>(initialStatus);
  const [apiReachable, setApiReachable] = React.useState(false);
  const [showApp, setShowApp] = React.useState(false);

  React.useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;

    const poll = async () => {
      try {
        const next = await getBootstrapStatus();
        if (cancelled) return;
        setApiReachable(true);
        setStatus(next);
        if (next.ready) {
          window.setTimeout(() => {
            if (!cancelled) setShowApp(true);
          }, 350);
          return;
        }
      } catch {
        if (cancelled) return;
        setApiReachable(false);
        setStatus((current) => ({
          ...current,
          phase: "starting",
          message: "Waiting for API server.",
        }));
      }
      timer = window.setTimeout(poll, 900);
    };

    poll();

    return () => {
      cancelled = true;
      if (timer) window.clearTimeout(timer);
    };
  }, []);

  if (showApp) {
    return <>{children}</>;
  }

  const progress = apiReachable ? Math.max(0, Math.min(100, status.progress || 0)) : 8;
  const phaseText = status.phase === "icons" ? "Configuring icon database" : "Configuring server";
  const countText = status.total
    ? `${status.completed.toLocaleString()} / ${status.total.toLocaleString()}`
    : "Checking connection";

  return (
    <main className="flex min-h-[100dvh] items-center justify-center bg-background px-5">
      <section className="w-full max-w-md">
        <div className="mb-8 flex items-center justify-center gap-3">
          <div className="flex h-11 w-11 items-center justify-center rounded-md bg-primary text-primary-foreground">
            {status.ready ? (
              <CheckCircle2 className="h-6 w-6" />
            ) : apiReachable ? (
              <Database className="h-6 w-6" />
            ) : (
              <ServerCog className="h-6 w-6" />
            )}
          </div>
          <div>
            <p className="text-sm font-medium text-muted-foreground">DocuMind</p>
            <h1 className="text-xl font-semibold tracking-normal">{phaseText}</h1>
          </div>
        </div>

        <div className="space-y-3">
          <div className="flex items-center justify-between text-sm">
            <span className="flex items-center gap-2 text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              {apiReachable ? "Configuration status received" : "Waiting for API server"}
            </span>
            <span className="font-medium tabular-nums">{countText}</span>
          </div>
          <div className="h-2 overflow-hidden rounded-sm bg-muted">
            <div
              className="h-full rounded-sm bg-primary transition-all duration-500 ease-out"
              style={{ width: `${progress}%` }}
            />
          </div>
          <p className="min-h-6 text-sm text-muted-foreground">
            {status.phase === "error"
              ? "Checking configuration status again."
              : "Preparing the server assets required for document generation."}
          </p>
        </div>
      </section>
    </main>
  );
}
