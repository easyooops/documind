import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

import { translate, type Locale, DEFAULT_LOCALE } from "@/i18n";

export function formatDate(
  date: string | Date,
  locale: Locale = DEFAULT_LOCALE
): string {
  const raw = typeof date === "string" ? date : date.toISOString();
  const d = new Date(
    raw.endsWith("Z") || raw.includes("+") || raw.includes("T") && raw.match(/[+-]\d{2}:\d{2}$/)
      ? raw
      : raw + "Z"
  );
  const now = new Date();
  const diff = now.getTime() - d.getTime();

  if (diff < 60000) return translate(locale, "time.justNow");
  if (diff < 3600000) {
    return translate(locale, "time.minutesAgo", {
      count: Math.floor(diff / 60000),
    });
  }
  if (diff < 86400000) {
    return translate(locale, "time.hoursAgo", {
      count: Math.floor(diff / 3600000),
    });
  }
  if (diff < 604800000) {
    return translate(locale, "time.daysAgo", {
      count: Math.floor(diff / 86400000),
    });
  }

  const dateLocale = locale === "ko" ? "ko-KR" : "en-US";
  return d.toLocaleDateString(dateLocale, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

export function formatFileSize(bytes: number): string {
  if (bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(1))} ${sizes[i]}`;
}

export function truncate(str: string, length: number): string {
  if (str.length <= length) return str;
  return str.slice(0, length) + "...";
}

export function generateId(): string {
  return globalThis.crypto?.randomUUID?.() || Math.random().toString(36).slice(2);
}
