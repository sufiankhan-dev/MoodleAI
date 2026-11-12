import type { Metadata, Viewport } from "next";

import "./globals.css";

import { AppShell } from "@/components/app-shell";
import { DashboardProvider } from "@/components/dashboard-provider";
import { ThemeProvider } from "@/components/theme-provider";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";

export const metadata: Metadata = {
  title: "MoodleAI — Local Study Assistant",
  description:
    "Local, loopback-only Moodle study assistant. Visible Chrome, local qwen3 model, no cloud AI.",
};

export const viewport: Viewport = {
  colorScheme: "light dark",
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#fafafa" },
    { media: "(prefers-color-scheme: dark)", color: "#0f1115" },
  ],
};

const themeScript = `
try {
  var stored = localStorage.getItem("moodleai-theme");
  var dark = stored ? stored === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
  if (dark) document.documentElement.classList.add("dark");
} catch (_) {}
`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body>
        <ThemeProvider>
          <TooltipProvider>
            <DashboardProvider>
              <AppShell>{children}</AppShell>
              <Toaster richColors closeButton />
            </DashboardProvider>
          </TooltipProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
