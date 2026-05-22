"use client";

import React, { useEffect, useState } from "react";
import { Clock, FileCheck } from "lucide-react";
import { ScrollArea } from "@/components/ui/scroll-area";
import { getDocumentVersions } from "@/lib/api";
import { formatDate } from "@/lib/utils";
import { useLocaleStore } from "@/stores/locale";
import { useTranslation } from "@/hooks/useTranslation";
import type { DocumentVersion } from "@/types";

interface VersionHistoryProps {
  jobId: string;
}

export function VersionHistory({ jobId }: VersionHistoryProps) {
  const [versions, setVersions] = useState<DocumentVersion[]>([]);
  const [loading, setLoading] = useState(true);
  const locale = useLocaleStore((s) => s.locale);
  const { t } = useTranslation();

  useEffect(() => {
    const load = async () => {
      try {
        const data = await getDocumentVersions(jobId);
        setVersions(data);
      } catch {
        setVersions([]);
      } finally {
        setLoading(false);
      }
    };
    load();
  }, [jobId]);

  if (loading) {
    return (
      <div className="p-3 text-xs text-muted-foreground">{t("document.loading")}</div>
    );
  }

  if (versions.length === 0) {
    return (
      <div className="p-3 text-xs text-muted-foreground text-center">
        {t("document.noVersions")}
      </div>
    );
  }

  return (
    <ScrollArea className="h-full">
      <div className="p-3 space-y-1">
        <h3 className="text-xs font-semibold text-muted-foreground mb-2 flex items-center gap-1">
          <Clock className="w-3 h-3" />
          {t("document.versionHistory")}
        </h3>
        {versions.map((v) => (
          <button
            key={v.id}
            className="w-full text-left p-2 rounded-lg hover:bg-accent transition-colors"
          >
            <div className="flex items-center gap-2">
              <FileCheck className="w-3.5 h-3.5 text-primary flex-shrink-0" />
              <div className="min-w-0">
                <p className="text-xs font-medium truncate">
                  v{v.versionNumber}
                  {v.fidelityScore && (
                    <span className="text-muted-foreground ml-1">
                      ({Math.round(v.fidelityScore * 100)}%)
                    </span>
                  )}
                </p>
                <p className="text-[10px] text-muted-foreground">
                  {formatDate(v.createdAt, locale)}
                </p>
              </div>
            </div>
          </button>
        ))}
      </div>
    </ScrollArea>
  );
}
