"use client";

import React, { useEffect, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Download, Minimize2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useDocumentStore } from "@/stores/document";
import { getPreviewUrl, getDownloadUrl } from "@/lib/api";
import { useTranslation } from "@/hooks/useTranslation";

export function FullscreenViewer() {
  const { currentJob, selectedVersionNumber, isFullscreen, setFullscreen } = useDocumentStore();
  const { t } = useTranslation();

  const handleEsc = useCallback(
    (e: KeyboardEvent) => {
      if (e.key === "Escape") setFullscreen(false);
    },
    [setFullscreen]
  );

  useEffect(() => {
    document.addEventListener("keydown", handleEsc);
    return () => document.removeEventListener("keydown", handleEsc);
  }, [handleEsc]);

  if (!isFullscreen || !currentJob) return null;

  return (
    <AnimatePresence>
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        className="fixed inset-0 z-[100] bg-background flex flex-col"
      >
        {/* Toolbar */}
        <div className="flex items-center justify-between px-4 py-2 border-b border-border">
          <h2 className="text-sm font-semibold">{t("document.previewTitle")}</h2>
          <div className="flex items-center gap-2">
            {currentJob.downloadUrl && (
              <Button size="sm" variant="outline" className="h-8" asChild>
                <a href={getDownloadUrl(currentJob.id, selectedVersionNumber)} download>
                  <Download className="w-3.5 h-3.5 mr-1" />
                  {t("document.download")}
                </a>
              </Button>
            )}
            <Button
              size="icon"
              variant="ghost"
              className="h-8 w-8"
              onClick={() => setFullscreen(false)}
            >
              <Minimize2 className="w-4 h-4" />
            </Button>
          </div>
        </div>

        {/* Viewer */}
        <div className="flex-1 overflow-hidden">
          <iframe
            src={getPreviewUrl(currentJob.id, selectedVersionNumber)}
            className="w-full h-full border-0"
            title="Full Screen Document Preview"
            sandbox="allow-same-origin allow-scripts"
          />
        </div>
      </motion.div>
    </AnimatePresence>
  );
}
