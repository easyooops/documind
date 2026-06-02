"use client";

import React from "react";
import { motion } from "framer-motion";
import {
  X,
  Download,
  Maximize2,
  History,
  FileText,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { useDocumentStore } from "@/stores/document";
import { DocumentViewer } from "./DocumentViewer";
import { VersionHistory } from "./VersionHistory";
import { getDocumentVersions, getDownloadUrl } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useTranslation } from "@/hooks/useTranslation";

interface DocumentPanelProps {
  isMobile?: boolean;
}

export function DocumentPanel({ isMobile }: DocumentPanelProps) {
  const {
    currentJob,
    versions,
    selectedVersionNumber,
    setVersions,
    selectVersion,
    closePanel,
    setFullscreen,
  } = useDocumentStore();
  const [showVersions, setShowVersions] = React.useState(false);
  const { t } = useTranslation();

  React.useEffect(() => {
    if (!currentJob?.id || currentJob.status !== "completed") return;
    getDocumentVersions(currentJob.id).then(setVersions).catch(() => setVersions([]));
  }, [currentJob?.id, currentJob?.status, setVersions]);

  if (!currentJob) return null;

  return (
    <div
      className={cn(
        "h-full flex flex-col bg-card/50",
        isMobile ? "w-full" : "w-[520px]"
      )}
    >
      {/* Panel Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-border">
        <div className="flex items-center gap-2">
          <FileText className="w-4 h-4 text-primary" />
          <h2 className="font-semibold text-sm">{t("document.generatedTitle")}</h2>
          {versions.length > 0 && (
            <select
              value={selectedVersionNumber ?? versions[0].versionNumber}
              onChange={(event) => selectVersion(Number(event.target.value))}
              className="h-7 rounded-md border border-input bg-background px-1.5 text-xs"
              aria-label={t("document.versionHistory")}
            >
              {versions.map((version) => (
                <option key={version.id} value={version.versionNumber}>
                  v{version.versionNumber}
                </option>
              ))}
            </select>
          )}
        </div>
        <div className="flex items-center gap-1">
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7"
            onClick={() => setShowVersions(!showVersions)}
          >
            <History className="w-3.5 h-3.5" />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7"
            onClick={() => setFullscreen(true)}
          >
            <Maximize2 className="w-3.5 h-3.5" />
          </Button>
          {currentJob.status === "completed" && (
            <Button
              variant="ghost"
              size="icon"
              className="h-7 w-7 text-primary hover:text-primary"
              asChild
            >
              <a href={getDownloadUrl(currentJob.id, selectedVersionNumber)} download>
                <Download className="w-3.5 h-3.5" />
              </a>
            </Button>
          )}
          <Button variant="ghost" size="icon" className="h-7 w-7" onClick={closePanel}>
            <X className="w-3.5 h-3.5" />
          </Button>
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 flex overflow-hidden">
        {/* Document Preview */}
        <div className={cn("flex-1 flex flex-col", showVersions && "border-r border-border")}>
          <DocumentViewer job={currentJob} />
        </div>

        {/* Version History Sidebar */}
        {showVersions && (
          <motion.div
            initial={{ width: 0, opacity: 0 }}
            animate={{ width: 200, opacity: 1 }}
            exit={{ width: 0, opacity: 0 }}
            className="overflow-hidden"
          >
            <VersionHistory jobId={currentJob.id} />
          </motion.div>
        )}
      </div>
    </div>
  );
}
