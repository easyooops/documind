"use client";

import React, { useState } from "react";
import { motion } from "framer-motion";
import { FileText, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useUserStore } from "@/stores/user";
import { identifyUser } from "@/lib/api";
import { generateId } from "@/lib/utils";
import { useTranslation } from "@/hooks/useTranslation";
import { LanguageSwitcher } from "@/components/layout/LanguageSwitcher";

export function UserIdentifyModal() {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const { setUser } = useUserStore();
  const { t } = useTranslation();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim() || !email.trim()) {
      setError(t("auth.errorNameEmailRequired"));
      return;
    }
    if (!email.includes("@")) {
      setError(t("auth.errorInvalidEmail"));
      return;
    }

    setLoading(true);
    setError("");

    try {
      const user = await identifyUser(name.trim(), email.trim());
      setUser(user);
    } catch {
      setUser({ id: generateId(), name: name.trim(), email: email.trim() });
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-gradient-to-br from-slate-50 via-indigo-50/40 to-white">
      <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_top,_var(--tw-gradient-stops))] from-indigo-100/60 via-transparent to-transparent" />

      <motion.div
        initial={{ opacity: 0, scale: 0.95, y: 20 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        transition={{ duration: 0.5, ease: "easeOut" }}
        className="relative w-full max-w-md mx-4"
      >
        <div className="glass-card rounded-2xl p-8 shadow-2xl">
          <div className="absolute top-4 right-4">
            <LanguageSwitcher />
          </div>

          <div className="flex flex-col items-center mb-8">
            <div className="w-14 h-14 rounded-xl bg-gradient-to-br from-indigo-500 to-purple-600 flex items-center justify-center mb-4 shadow-lg shadow-indigo-500/25">
              <FileText className="w-7 h-7 text-white" />
            </div>
            <h1 className="text-2xl font-bold text-foreground">DocuMind</h1>
            <p className="text-sm text-muted-foreground mt-1 flex items-center gap-1">
              <Sparkles className="w-3.5 h-3.5" />
              {t("auth.tagline")}
            </p>
          </div>

          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-foreground mb-1.5">
                {t("auth.name")}
              </label>
              <Input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder={t("auth.namePlaceholder")}
                className="bg-secondary/50"
                autoFocus
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-foreground mb-1.5">
                {t("auth.email")}
              </label>
              <Input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="hong@example.com"
                className="bg-secondary/50"
              />
            </div>

            {error && (
              <p className="text-sm text-destructive">{error}</p>
            )}

            <Button
              type="submit"
              className="w-full bg-gradient-to-r from-indigo-500 to-purple-600 hover:from-indigo-600 hover:to-purple-700 text-white shadow-lg shadow-indigo-500/25"
              disabled={loading}
            >
              {loading ? t("auth.connecting") : t("auth.getStarted")}
            </Button>
          </form>

          <p className="text-xs text-muted-foreground text-center mt-6">
            {t("auth.privacyNotice")}
          </p>
        </div>
      </motion.div>
    </div>
  );
}
