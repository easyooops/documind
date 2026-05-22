import type { Metadata, Viewport } from "next";
import "./globals.css";
import { LocaleProvider } from "@/components/layout/LocaleProvider";

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
};

export const metadata: Metadata = {
  title: "DocuMind — AI Document Generation Platform",
  description:
    "Agentic AI platform that generates expert-level documents from natural language requests.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <head>
        <link
          href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable-dynamic-subset.min.css"
          rel="stylesheet"
        />
      </head>
      <body className="font-sans text-[15px] sm:text-base">
        <LocaleProvider>{children}</LocaleProvider>
      </body>
    </html>
  );
}
