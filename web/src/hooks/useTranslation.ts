"use client";

import { useCallback } from "react";
import { translate, type Locale } from "@/i18n";
import { useLocaleStore } from "@/stores/locale";

export function useTranslation() {
  const locale = useLocaleStore((s) => s.locale);
  const setLocale = useLocaleStore((s) => s.setLocale);

  const t = useCallback(
    (key: string, params?: Record<string, string | number>) =>
      translate(locale, key, params),
    [locale]
  );

  return { t, locale, setLocale };
}

export type { Locale };
