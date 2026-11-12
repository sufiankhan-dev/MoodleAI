"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Activity,
  ClipboardList,
  FolderTree,
  LayoutDashboard,
  MessageSquare,
  Moon,
  PanelLeft,
  Square,
  Sun,
} from "lucide-react";
import * as React from "react";

import { useDashboard } from "@/components/dashboard-provider";
import { ApprovalDialog } from "@/components/approval-dialog";
import { StatusBadges } from "@/components/status-badges";
import { useTheme } from "@/components/theme-provider";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { cn, truncate } from "@/lib/utils";

const NAV = [
  { href: "/", label: "Overview", icon: LayoutDashboard },
  { href: "/chat", label: "Chat", icon: MessageSquare },
  { href: "/assignments", label: "Assignments", icon: ClipboardList },
  { href: "/files", label: "Files", icon: FolderTree },
  { href: "/activity", label: "Activity", icon: Activity },
];

function NavList({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();
  const { pendingAssignments, approvals } = useDashboard();

  return (
    <nav className="flex flex-col gap-0.5 px-3">
      {NAV.map((item) => {
        const active = item.href === "/" ? pathname === "/" : pathname.startsWith(item.href);
        const Icon = item.icon;
        const badge =
          item.href === "/assignments" && pendingAssignments > 0
            ? pendingAssignments
            : item.href === "/chat" && approvals.length > 0
              ? approvals.length
              : null;

        return (
          <Link
            key={item.href}
            href={item.href}
            onClick={onNavigate}
            className={cn(
              "group flex items-center gap-2.5 rounded-md px-2.5 py-2 text-sm font-medium transition-colors",
              active
                ? "bg-sidebar-accent text-sidebar-accent-foreground"
                : "text-muted-foreground hover:bg-sidebar-accent/60 hover:text-sidebar-accent-foreground",
            )}
          >
            <Icon
              className={cn(
                "size-4 shrink-0",
                active ? "text-foreground" : "text-muted-foreground group-hover:text-foreground",
              )}
            />
            <span className="flex-1">{item.label}</span>
            {badge ? (
              <Badge variant={item.href === "/chat" ? "destructive" : "secondary"} className="h-5 min-w-5 rounded-full px-1.5 text-[0.65rem]">
                {badge}
              </Badge>
            ) : null}
          </Link>
        );
      })}
    </nav>
  );
}

function SidebarFooter() {
  const { ollama, browser, tools } = useDashboard();
  const browserReady = browser?.state === "ready";

  return (
    <div className="px-3 pb-3">
      <Separator className="mb-3" />
      <div className="space-y-2 px-1 text-[0.7rem] text-muted-foreground">
        <div className="flex items-center justify-between gap-2">
          <span>Local model</span>
          <span className={cn("font-medium", ollama?.reachable ? "text-emerald-600 dark:text-emerald-400" : "text-amber-600 dark:text-amber-400")}>
            {ollama?.reachable ? ollama.model : "offline"}
          </span>
        </div>
        <div className="flex items-center justify-between gap-2">
          <span>Moodle</span>
          <span className={cn("font-medium", browserReady ? "text-emerald-600 dark:text-emerald-400" : "text-muted-foreground")}>
            {browserReady ? "connected" : (browser?.state ?? "disconnected").replace("_", " ")}
          </span>
        </div>
        <div className="flex items-center justify-between gap-2">
          <span>{tools.length} tools</span>
          <span>127.0.0.1 only</span>
        </div>
      </div>
    </div>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const { browser, running, stopAgent } = useDashboard();
  const { theme, toggle } = useTheme();
  const [menuOpen, setMenuOpen] = React.useState(false);

  const page = browser?.page;
  const title =
    pathname === "/"
      ? "Overview"
      : pathname.startsWith("/chat")
        ? "Chat"
        : pathname.startsWith("/assignments")
          ? "Assignments"
          : pathname.startsWith("/files")
            ? "Files"
            : pathname.startsWith("/activity")
              ? "Activity"
              : "MoodleAI";

  React.useEffect(() => {
    setMenuOpen(false);
  }, [pathname]);

  return (
    <div className="bg-background flex h-dvh overflow-hidden">
      {/* desktop sidebar */}
      <aside className="bg-sidebar text-sidebar-foreground border-sidebar-border hidden w-60 shrink-0 flex-col border-r lg:flex">
        <div className="flex h-16 items-center gap-2.5 px-4">
          <div className="bg-primary text-primary-foreground flex size-8 items-center justify-center rounded-lg text-sm font-semibold shadow-sm">
            M
          </div>
          <div className="leading-tight">
            <div className="text-sm font-semibold tracking-tight">MoodleAI</div>
            <div className="text-muted-foreground text-[0.68rem]">Local study assistant</div>
          </div>
        </div>
        <NavList />
        <div className="flex-1" />
        <SidebarFooter />
      </aside>

      {/* mobile drawer */}
      {menuOpen ? (
        <div className="fixed inset-0 z-40 lg:hidden">
          <button
            type="button"
            aria-label="Close menu"
            className="absolute inset-0 bg-black/40"
            onClick={() => setMenuOpen(false)}
          />
          <aside className="bg-sidebar text-sidebar-foreground border-sidebar-border absolute inset-y-0 left-0 flex w-64 flex-col border-r shadow-xl">
            <div className="flex h-16 items-center gap-2.5 px-4">
              <div className="bg-primary text-primary-foreground flex size-8 items-center justify-center rounded-lg text-sm font-semibold">
                M
              </div>
              <div className="text-sm font-semibold">MoodleAI</div>
            </div>
            <NavList onNavigate={() => setMenuOpen(false)} />
            <div className="flex-1" />
            <SidebarFooter />
          </aside>
        </div>
      ) : null}

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="bg-background/85 supports-[backdrop-filter]:bg-background/70 sticky top-0 z-30 flex h-16 shrink-0 items-center gap-3 border-b px-4 backdrop-blur lg:px-6">
          <Button
            variant="ghost"
            size="icon-sm"
            className="lg:hidden"
            aria-label="Open menu"
            onClick={() => setMenuOpen(true)}
          >
            <PanelLeft className="size-4" />
          </Button>

          <div className="min-w-0 flex-1">
            <h1 className="truncate text-sm font-semibold tracking-tight">{title}</h1>
            <p className="text-muted-foreground truncate text-[0.7rem]">
              {page?.title
                ? truncate(page.title, 90)
                : page?.url
                  ? truncate(page.url, 90)
                  : "No Moodle page open yet"}
            </p>
          </div>

          <div className="hidden md:block">
            <StatusBadges />
          </div>

          <div className="flex items-center gap-1.5">
            {running ? (
              <Button variant="destructive" size="sm" onClick={() => void stopAgent()}>
                <Square className="size-3.5" />
                Stop
              </Button>
            ) : null}
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="Toggle theme"
              title="Toggle theme"
              onClick={toggle}
            >
              {theme === "dark" ? <Sun className="size-4" /> : <Moon className="size-4" />}
            </Button>
          </div>
        </header>

        <div className="md:hidden border-b px-4 py-2">
          <StatusBadges />
        </div>

        <main className="min-h-0 flex-1 overflow-hidden">
          {children}
        </main>
      </div>

      <ApprovalDialog />
    </div>
  );
}
