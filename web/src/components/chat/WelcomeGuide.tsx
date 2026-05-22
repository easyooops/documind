"use client";

import React, { useMemo } from "react";
import { motion } from "framer-motion";
import {
  FileText,
  Presentation,
  FileSpreadsheet,
  Code2,
  BookOpen,
  Sparkles,
  ArrowRight,
} from "lucide-react";
import { useSessionStore } from "@/stores/session";
import { useTranslation } from "@/hooks/useTranslation";
import type { DocumentFormat } from "@/types";

export function WelcomeGuide() {
  const { setSelectedFormat } = useSessionStore();
  const { t } = useTranslation();

  const examplePrompts = useMemo(
    () => [
      {
        text: t("welcome.prompts.cloudProposal"),
        format: "pptx" as DocumentFormat,
        icon: Presentation,
      },
      {
        text: t("welcome.prompts.techDesign"),
        format: "docx" as DocumentFormat,
        icon: FileText,
      },
      {
        text: t("welcome.prompts.salesReport"),
        format: "xlsx" as DocumentFormat,
        icon: FileSpreadsheet,
      },
      {
        text: t("welcome.prompts.apiGuide"),
        format: "md" as DocumentFormat,
        icon: Code2,
      },
    ],
    [t]
  );

  const features = useMemo(
    () => [
      {
        icon: Sparkles,
        title: t("welcome.features.pipelineTitle"),
        desc: t("welcome.features.pipelineDesc"),
      },
      {
        icon: BookOpen,
        title: t("welcome.features.formatsTitle"),
        desc: t("welcome.features.formatsDesc"),
      },
      {
        icon: Presentation,
        title: t("welcome.features.templateTitle"),
        desc: t("welcome.features.templateDesc"),
      },
    ],
    [t]
  );

  return (
    <div className="flex-1 flex items-center justify-center p-4 sm:p-8 overflow-y-auto">
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5 }}
        className="max-w-2xl w-full space-y-8"
      >
        {/* Hero */}
        <div className="text-center space-y-3">
          <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-primary/10 text-primary text-xs font-medium">
            <Sparkles className="w-3 h-3" />
            Agentic AI Document Generation
          </div>
          <h1 className="text-2xl sm:text-3xl font-bold text-foreground tracking-tight">
            {t("welcome.heroTitle")}
          </h1>
          <p className="text-muted-foreground max-w-md mx-auto">
            {t("welcome.heroSubtitle")}
          </p>
        </div>

        {/* Features */}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 sm:gap-4">
          {features.map((f, i) => (
            <motion.div
              key={i}
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.1 * (i + 1) }}
              className="glass-card rounded-xl p-4 text-center"
            >
              <div className="w-9 h-9 rounded-lg bg-primary/10 flex items-center justify-center mx-auto mb-2">
                <f.icon className="w-4.5 h-4.5 text-primary" />
              </div>
              <h3 className="text-sm font-medium mb-1">{f.title}</h3>
              <p className="text-xs text-muted-foreground">{f.desc}</p>
            </motion.div>
          ))}
        </div>

        {/* Example Prompts */}
        <div className="space-y-2">
          <p className="text-sm font-medium text-muted-foreground px-1">
            {t("welcome.examplePrompts")}
          </p>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            {examplePrompts.map((p, i) => (
              <motion.button
                key={i}
                initial={{ opacity: 0, x: -10 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ delay: 0.2 + 0.05 * i }}
                onClick={() => setSelectedFormat(p.format)}
                className="group flex items-center gap-3 p-3 rounded-xl border border-border hover:border-primary/30 hover:bg-accent/50 transition-all duration-200 text-left"
              >
                <div className="w-8 h-8 rounded-lg bg-secondary flex items-center justify-center flex-shrink-0">
                  <p.icon className="w-4 h-4 text-muted-foreground" />
                </div>
                <span className="text-sm text-foreground flex-1 line-clamp-2">
                  {p.text}
                </span>
                <ArrowRight className="w-3.5 h-3.5 text-muted-foreground opacity-0 group-hover:opacity-100 transition-opacity" />
              </motion.button>
            ))}
          </div>
        </div>
      </motion.div>
    </div>
  );
}
