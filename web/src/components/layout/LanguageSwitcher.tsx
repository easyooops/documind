"use client";

import { Languages } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useTranslation } from "@/hooks/useTranslation";
import type { Locale } from "@/i18n";
import { cn } from "@/lib/utils";

interface LanguageSwitcherProps {
  collapsed?: boolean;
}

export function LanguageSwitcher({ collapsed }: LanguageSwitcherProps) {
  const { t, locale, setLocale } = useTranslation();

  const toggle = () => setLocale(locale === "en" ? "ko" : "en");

  if (collapsed) {
    return (
      <Button
        variant="ghost"
        size="icon"
        className="h-8 w-8 mx-auto"
        onClick={toggle}
        title={t("language.label")}
        aria-label={t("language.label")}
      >
        <Languages className="w-4 h-4" />
      </Button>
    );
  }

  return (
    <div
      className="flex w-full items-center gap-0.5 rounded-lg border border-border p-0.5 bg-muted/50"
      role="group"
      aria-label={t("language.label")}
    >
      {(["en", "ko"] as Locale[]).map((code) => (
        <button
          key={code}
          type="button"
          onClick={() => setLocale(code)}
          className={cn(
            "flex-1 px-2 py-1.5 text-xs font-semibold rounded-md transition-all duration-200",
            locale === code
              ? "bg-primary text-primary-foreground shadow-sm"
              : "text-muted-foreground hover:text-foreground hover:bg-background/60"
          )}
          aria-pressed={locale === code}
        >
          {t(`language.${code}`)}
        </button>
      ))}
    </div>
  );
}
