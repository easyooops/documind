import { create } from "zustand";
import {
  DEFAULT_LOCALE,
  getStoredLocale,
  LOCALE_STORAGE_KEY,
  translate,
  type Locale,
} from "@/i18n";

function applyDocumentLocale(locale: Locale) {
  if (typeof document === "undefined") return;
  document.documentElement.lang = locale;
  document.title = translate(locale, "meta.title");
}

interface LocaleState {
  locale: Locale;
  setLocale: (locale: Locale) => void;
  hydrate: () => void;
}

export const useLocaleStore = create<LocaleState>((set) => ({
  locale: DEFAULT_LOCALE,
  setLocale: (locale) => {
    if (typeof window !== "undefined") {
      localStorage.setItem(LOCALE_STORAGE_KEY, locale);
      applyDocumentLocale(locale);
    }
    set({ locale });
  },
  hydrate: () => {
    const stored = getStoredLocale();
    const locale = stored ?? DEFAULT_LOCALE;
    applyDocumentLocale(locale);
    set({ locale });
  },
}));
