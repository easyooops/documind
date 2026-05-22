"use client";

import React from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Loader2, CheckCircle2, AlertCircle } from "lucide-react";
import { JOB_PHASE_I18N_KEYS, type JobPhase } from "@/types";
import { useTranslation } from "@/hooks/useTranslation";
import { useSessionStore, type NodeProgress } from "@/stores/session";
import { cn } from "@/lib/utils";

interface ProgressIndicatorProps {
  phase: string | null;
  progress: number;
}

const PHASE_ORDER: JobPhase[] = [
  "planning",
  "designing",
  "generating",
  "validating",
  "converting",
  "qa",
  "exporting",
];

export function ProgressIndicator({ phase, progress }: ProgressIndicatorProps) {
  const { t } = useTranslation();
  const { currentNode, currentNodeDescription, currentNodeElapsed, completedNodes, isGenerating } =
    useSessionStore();
  const currentIndex = phase ? PHASE_ORDER.indexOf(phase as JobPhase) : 0;
  const isDone = phase === "done";

  const phaseLabel = isDone
    ? t("progress.complete") ?? "완료"
    : phase && phase in JOB_PHASE_I18N_KEYS
      ? t(JOB_PHASE_I18N_KEYS[phase as JobPhase])
      : phase ?? t("progress.preparing");

  const recentNodes = completedNodes;

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      className="flex gap-3"
    >
      <div className={cn(
        "w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0",
        isDone
          ? "bg-gradient-to-br from-emerald-500 to-green-600"
          : "bg-gradient-to-br from-indigo-500 to-purple-600"
      )}>
        {isDone ? (
          <CheckCircle2 className="w-4 h-4 text-white" />
        ) : (
          <Loader2 className="w-4 h-4 text-white animate-spin" />
        )}
      </div>

      <div className="glass-card rounded-2xl rounded-bl-md px-4 py-3 max-w-md w-full">
        <p className="text-sm font-medium mb-2 flex items-center gap-2">
          <span className={isDone ? "" : "animate-pulse-subtle"}>{phaseLabel}</span>
          <span className="text-xs text-muted-foreground">
            {Math.round(progress * 100)}%
          </span>
        </p>

        {/* Current Node Activity with elapsed time */}
        <AnimatePresence mode="wait">
          {currentNodeDescription && isGenerating && (
            <motion.div
              key={`${currentNode}-${currentNodeDescription}`}
              initial={{ opacity: 0, x: -4 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: 4 }}
              transition={{ duration: 0.15 }}
              className="flex items-center gap-2 mb-2 px-2 py-1.5 rounded-lg bg-primary/5 border border-primary/10"
            >
              <Loader2 className="w-3 h-3 text-primary animate-spin flex-shrink-0" />
              <span className="text-xs text-foreground font-medium truncate flex-1">
                {currentNodeDescription}
              </span>
              {currentNodeElapsed > 0 && (
                <span className="text-[10px] text-muted-foreground tabular-nums flex-shrink-0">
                  {currentNodeElapsed.toFixed(0)}s
                </span>
              )}
            </motion.div>
          )}
        </AnimatePresence>

        {/* Completed Nodes Log */}
        {recentNodes.length > 0 && (
          <div className="space-y-0.5 mb-2">
            {recentNodes.map((node, i) => (
              <CompletedNodeItem key={`${node.node}-${i}`} node={node} />
            ))}
          </div>
        )}

        {/* Phase Progress Steps */}
        <div className="flex gap-1">
          {PHASE_ORDER.map((p, i) => (
            <div
              key={p}
              className={`h-1.5 flex-1 rounded-full transition-all duration-500 ${
                isDone || i < currentIndex
                  ? "bg-primary"
                  : i === currentIndex
                  ? "bg-primary/60 animate-pulse-subtle"
                  : "bg-secondary"
              }`}
            />
          ))}
        </div>

        <div className="flex justify-between mt-2">
          <span className="text-[10px] text-muted-foreground">
            {t("progress.startLabel")}
          </span>
          <span className="text-[10px] text-muted-foreground">
            {t("progress.endLabel")}
          </span>
        </div>
      </div>
    </motion.div>
  );
}

function CompletedNodeItem({ node }: { node: NodeProgress }) {
  return (
    <motion.div
      initial={{ opacity: 0, height: 0 }}
      animate={{ opacity: 1, height: "auto" }}
      className="flex items-center gap-1.5 px-1"
    >
      {node.status === "completed" ? (
        <CheckCircle2 className="w-3 h-3 text-emerald-500 flex-shrink-0" />
      ) : (
        <AlertCircle className="w-3 h-3 text-amber-500 flex-shrink-0" />
      )}
      <span className="text-[11px] text-muted-foreground truncate flex-1">
        {node.description}
      </span>
      {node.elapsedSeconds != null && node.elapsedSeconds > 0 && (
        <span className="text-[10px] text-muted-foreground/70 tabular-nums flex-shrink-0">
          {node.elapsedSeconds.toFixed(1)}s
        </span>
      )}
    </motion.div>
  );
}
