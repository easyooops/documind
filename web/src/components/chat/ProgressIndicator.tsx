"use client";

import React from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Loader2, CheckCircle2, AlertCircle, ChevronDown, ChevronRight } from "lucide-react";
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
  const { completedNodes, isGenerating, toggleNodeExpanded } =
    useSessionStore();
  const currentIndex = phase ? PHASE_ORDER.indexOf(phase as JobPhase) : 0;
  const isDone = phase === "done";

  const phaseLabel = isDone
    ? t("progress.complete") ?? "완료"
    : phase && phase in JOB_PHASE_I18N_KEYS
      ? t(JOB_PHASE_I18N_KEYS[phase as JobPhase])
      : phase ?? t("progress.preparing");

  const nodeItems = completedNodes;

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

        {/* Node Activity Accordion */}
        {nodeItems.length > 0 && (
          <div className="space-y-1 mb-2">
            {nodeItems.map((node, i) => (
              <NodeProgressItem
                key={`${node.node}-${i}`}
                node={node}
                isGenerating={isGenerating}
                onToggle={() => toggleNodeExpanded(node.node)}
              />
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

function NodeProgressItem({
  node,
  isGenerating,
  onToggle,
}: {
  node: NodeProgress;
  isGenerating: boolean;
  onToggle: () => void;
}) {
  const isRunning = node.status === "running" && isGenerating;
  const expanded = isRunning || node.expanded;
  const StatusIcon = node.status === "completed" ? CheckCircle2 : node.status === "error" ? AlertCircle : Loader2;
  const summaryItems = node.summaryItems ?? [];

  return (
    <motion.div
      initial={{ opacity: 0, height: 0 }}
      animate={{ opacity: 1, height: "auto" }}
      className={cn(
        "rounded-lg border transition-colors",
        isRunning ? "border-primary/15 bg-primary/5" : "border-transparent bg-transparent"
      )}
    >
      <button
        type="button"
        onClick={onToggle}
        className="w-full flex items-center gap-1.5 px-1.5 py-1 text-left"
        aria-expanded={expanded}
      >
        {expanded ? (
          <ChevronDown className="w-3 h-3 text-muted-foreground flex-shrink-0" />
        ) : (
          <ChevronRight className="w-3 h-3 text-muted-foreground flex-shrink-0" />
        )}
        <StatusIcon
          className={cn(
            "w-3 h-3 flex-shrink-0",
            node.status === "completed" && "text-emerald-500",
            node.status === "error" && "text-amber-500",
            isRunning && "text-primary animate-spin"
          )}
        />
        <span className={cn(
          "text-[11px] truncate flex-1",
          isRunning ? "text-foreground font-medium" : "text-muted-foreground"
        )}>
          {node.description}
        </span>
        {node.elapsedSeconds != null && node.elapsedSeconds > 0 && (
          <span className="text-[10px] text-muted-foreground/70 tabular-nums flex-shrink-0">
            {node.elapsedSeconds.toFixed(node.status === "running" ? 0 : 1)}s
          </span>
        )}
      </button>

      <AnimatePresence initial={false}>
        {expanded && summaryItems.length > 0 && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            className="px-6 pb-1.5 space-y-1"
          >
            {summaryItems.map((item, i) => (
              <div key={`${item}-${i}`} className="flex items-start gap-1.5">
                <span className={cn(
                  "mt-1.5 w-1.5 h-1.5 rounded-full flex-shrink-0",
                  node.status === "error" ? "bg-amber-500" : "bg-muted-foreground/35"
                )} />
                <span className="text-[10px] leading-relaxed text-muted-foreground flex-1">
                  {item}
                </span>
              </div>
            ))}
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}
