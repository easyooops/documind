"use client";

import { AppShell } from "@/components/layout/AppShell";
import { BootstrapGate } from "@/components/layout/BootstrapGate";

export default function Home() {
  return (
    <BootstrapGate>
      <AppShell />
    </BootstrapGate>
  );
}
