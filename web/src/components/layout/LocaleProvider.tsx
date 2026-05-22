"use client";

import { useEffect } from "react";
import { useLocaleStore } from "@/stores/locale";

export function LocaleProvider({ children }: { children: React.ReactNode }) {
  const hydrate = useLocaleStore((s) => s.hydrate);

  useEffect(() => {
    hydrate();
  }, [hydrate]);

  return <>{children}</>;
}
