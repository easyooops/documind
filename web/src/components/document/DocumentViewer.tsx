"use client";

import React from "react";
import { FileText } from "lucide-react";
import type { GenerationJob } from "@/types";
import { getPreviewUrl } from "@/lib/api";
import { useTranslation } from "@/hooks/useTranslation";

interface DocumentViewerProps {
  job: GenerationJob;
}

export function DocumentViewer({ job }: DocumentViewerProps) {
  const { t } = useTranslation();
  const previewUrl = getPreviewUrl(job.id);

  if (job.status !== "completed") {
    return (
      <div className="flex-1 flex items-center justify-center">
        <div className="text-center space-y-2">
          <div className="w-12 h-12 rounded-xl bg-secondary flex items-center justify-center mx-auto">
            <FileText className="w-6 h-6 text-muted-foreground" />
          </div>
          <p className="text-sm text-muted-foreground">{t("document.preparing")}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 flex flex-col bg-secondary/20 overflow-hidden min-h-0">
      <iframe
        src={previewUrl}
        className="w-full h-full border-0 min-h-0"
        style={{ flex: "1 1 0%", transform: "scale(1)", transformOrigin: "top center" }}
        title="Document Preview"
        sandbox="allow-same-origin allow-scripts"
      />
    </div>
  );
}
