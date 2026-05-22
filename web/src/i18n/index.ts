import en from "./locales/en.json";
import ko from "./locales/ko.json";

export type Locale = "en" | "ko";

export const DEFAULT_LOCALE: Locale = "en";

export const LOCALES: Locale[] = ["en", "ko"];

const messages: Record<Locale, Record<string, unknown>> = {
  en,
  ko,
};

export function getMessage(locale: Locale, key: string): string | undefined {
  const parts = key.split(".");
  let current: unknown = messages[locale];

  for (const part of parts) {
    if (current == null || typeof current !== "object") return undefined;
    current = (current as Record<string, unknown>)[part];
  }

  return typeof current === "string" ? current : undefined;
}

export function translate(
  locale: Locale,
  key: string,
  params?: Record<string, string | number>
): string {
  const template = getMessage(locale, key) ?? getMessage(DEFAULT_LOCALE, key) ?? key;

  if (!params) return template;

  return template.replace(/\{\{(\w+)\}\}/g, (_, name: string) => {
    const value = params[name];
    return value !== undefined ? String(value) : `{{${name}}}`;
  });
}

export const LOCALE_STORAGE_KEY = "documind-locale";

export function isLocale(value: string): value is Locale {
  return value === "en" || value === "ko";
}

export function getStoredLocale(): Locale | null {
  if (typeof window === "undefined") return null;
  const stored = localStorage.getItem(LOCALE_STORAGE_KEY);
  return stored && isLocale(stored) ? stored : null;
}
