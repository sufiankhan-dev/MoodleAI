"use client";

import Link from "next/link";
import {
  Activity,
  ArrowRight,
  ClipboardList,
  FolderTree,
  GraduationCap,
  MessageSquare,
  Plus,
  ServerCrash,
  Wifi,
} from "lucide-react";
import * as React from "react";

import { ActivityFeed } from "@/components/activity-feed";
import { AssignmentsTable } from "@/components/assignments-table";
import { ConnectionCard } from "@/components/connection-card";
import { CoursesGrid } from "@/components/courses-grid";
import { useDashboard } from "@/components/dashboard-provider";
import { FileTree } from "@/components/file-tree";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import type { FileNode } from "@/lib/types";
import { cn } from "@/lib/utils";

function countFiles(nodes: FileNode[]): number {
  let total = 0;
  for (const node of nodes) {
    if (node.type === "file") total += 1;
    if (node.type === "dir" && node.children) total += countFiles(node.children);
  }
  return total;
}

function StatCard({
  label,
  value,
  hint,
  icon: Icon,
  href,
  tone = "default",
}: {
  label: string;
  value: React.ReactNode;
  hint: string;
  icon: React.ComponentType<{ className?: string }>;
  href: string;
  tone?: "default" | "warning" | "success";
}) {
  return (
    <Link
      href={href}
      className="group bg-card hover:border-foreground/15 rounded-xl border p-4 shadow-xs transition-colors"
    >
      <div className="flex items-start justify-between gap-2">
        <span
          className={cn(
            "flex size-8 items-center justify-center rounded-lg border",
            tone === "warning"
              ? "border-amber-500/25 bg-amber-500/10 text-amber-600 dark:text-amber-400"
              : tone === "success"
                ? "border-emerald-500/25 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
                : "bg-muted text-muted-foreground",
          )}
        >
          <Icon className="size-4" />
        </span>
        <ArrowRight className="text-muted-foreground/40 group-hover:text-foreground size-3.5 transition-colors" />
      </div>
      <div className="mt-3 text-2xl font-semibold tracking-tight tabular-nums">{value}</div>
      <div className="text-sm font-medium">{label}</div>
      <div className="text-muted-foreground mt-0.5 text-[0.68rem]">{hint}</div>
    </Link>
  );
}

export default function OverviewPage() {
  const {
    courses,
    assignments,
    files,
    activity,
    tools,
    agent,
    running,
    connected,
    offline,
    pendingAssignments,
    browser,
  } = useDashboard();

  const fileCount = countFiles(files.workspace) + countFiles(files.generated);
  const browserReady = browser?.state === "ready";

  return (
    <div className="pretty-scroll h-full overflow-y-auto">
      <div className="mx-auto max-w-7xl space-y-5 p-4 lg:p-6">
        {offline ? (
          <div className="flex items-start gap-3 rounded-lg border border-red-500/30 bg-red-500/5 p-3 text-xs text-red-700 dark:text-red-300">
            <ServerCrash className="mt-0.5 size-4 shrink-0" />
            <div>
              <p className="font-medium">Backend unreachable</p>
              <p className="mt-0.5">
                Start it with <code className="bg-red-500/10 rounded px-1">python main.py</code>,
                then reload this tab. The dashboard proxies every request to 127.0.0.1:8765.
              </p>
            </div>
          </div>
        ) : null}

        <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <StatCard
            label="Pending assignments"
            value={pendingAssignments}
            hint={`${assignments.length} saved in total`}
            icon={ClipboardList}
            href="/assignments"
            tone={pendingAssignments > 0 ? "warning" : "success"}
          />
          <StatCard
            label="Courses"
            value={courses.length}
            hint="Read from your Moodle session"
            icon={GraduationCap}
            href="/assignments"
          />
          <StatCard
            label="Study files"
            value={fileCount}
            hint="Workspace + generated"
            icon={FolderTree}
            href="/files"
          />
          <StatCard
            label="Agent"
            value={<span className="capitalize">{running ? agent.status.replace("_", " ") : "Idle"}</span>}
            hint={connected ? "Live updates connected" : "Reconnecting…"}
            icon={Activity}
            href="/chat"
            tone={browserReady ? "success" : "default"}
          />
        </section>

        <section className="grid gap-5 xl:grid-cols-3">
          <ConnectionCard className="xl:col-span-2" />

          <Card className="gap-0">
            <CardHeader className="pb-4">
              <CardTitle className="flex items-center gap-2 text-sm">
                <MessageSquare className="text-muted-foreground size-4" />
                Agent session
              </CardTitle>
              <CardDescription className="text-xs">
                Local qwen3:8b · 12-round safety bound
              </CardDescription>
              <CardAction>
                <Badge variant={connected ? "success" : "muted"} className="gap-1.5">
                  <Wifi className="size-3" />
                  {connected ? "live" : "offline"}
                </Badge>
              </CardAction>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid grid-cols-2 gap-3 text-xs">
                <div className="rounded-lg border bg-muted/30 p-3">
                  <div className="text-muted-foreground text-[0.65rem] uppercase tracking-wide">
                    Tools
                  </div>
                  <div className="mt-1 text-lg font-semibold tabular-nums">{tools.length}</div>
                </div>
                <div className="rounded-lg border bg-muted/30 p-3">
                  <div className="text-muted-foreground text-[0.65rem] uppercase tracking-wide">
                    Activity
                  </div>
                  <div className="mt-1 text-lg font-semibold tabular-nums">{activity.length}</div>
                </div>
              </div>

              <div className="space-y-1.5 text-xs">
                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">Moodle</span>
                  <span className="font-medium capitalize">
                    {(browser?.state ?? "disconnected").replace("_", " ")}
                  </span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">Writes</span>
                  <span className="font-medium">approval required</span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-muted-foreground">Network</span>
                  <span className="font-medium">127.0.0.1 only</span>
                </div>
              </div>

              <Button asChild className="w-full">
                <Link href="/chat">
                  <Plus className="size-4" />
                  Start a conversation
                </Link>
              </Button>

              <p className="text-muted-foreground text-[0.65rem] leading-relaxed">
                The agent reads Moodle in visible Chrome. It never submits, uploads, grades,
                deletes, or stores your password.
              </p>
            </CardContent>
          </Card>
        </section>

        <section className="grid gap-5 xl:grid-cols-5">
          <CoursesGrid className="xl:col-span-3" />
          <Card className="gap-0 xl:col-span-2">
            <CardHeader className="flex-row items-center justify-between pb-3">
              <div className="space-y-1">
                <CardTitle className="flex items-center gap-2 text-sm">
                  <FolderTree className="text-muted-foreground size-4" />
                  Latest files
                </CardTitle>
                <CardDescription className="text-xs">Workspace and generated</CardDescription>
              </div>
              <CardAction>
                <Button asChild variant="ghost" size="sm">
                  <Link href="/files">All files</Link>
                </Button>
              </CardAction>
            </CardHeader>
            <CardContent>
              <Tabs defaultValue="workspace">
                <TabsList className="w-full">
                  <TabsTrigger value="workspace">Workspace</TabsTrigger>
                  <TabsTrigger value="generated">Generated</TabsTrigger>
                </TabsList>
                <TabsContent value="workspace">
                  <FileTree nodes={files.workspace} className="max-h-64" />
                </TabsContent>
                <TabsContent value="generated">
                  <FileTree nodes={files.generated} className="max-h-64" />
                </TabsContent>
              </Tabs>
            </CardContent>
          </Card>
        </section>

        <AssignmentsTable limit={6} />

        <section className="grid gap-5 xl:grid-cols-2">
          <Card className="gap-0">
            <CardHeader className="flex-row items-center justify-between pb-3">
              <div className="space-y-1">
                <CardTitle className="flex items-center gap-2 text-sm">
                  <Activity className="text-muted-foreground size-4" />
                  Recent activity
                </CardTitle>
                <CardDescription className="text-xs">Newest first</CardDescription>
              </div>
              <CardAction>
                <Button asChild variant="ghost" size="sm">
                  <Link href="/activity">Full log</Link>
                </Button>
              </CardAction>
            </CardHeader>
            <CardContent>
              <ActivityFeed limit={7} />
            </CardContent>
          </Card>

          <Card className="gap-0">
            <CardHeader className="pb-3">
              <CardTitle className="text-sm">Private by design</CardTitle>
              <CardDescription className="text-xs">
                What this app deliberately cannot do
              </CardDescription>
            </CardHeader>
            <CardContent>
              <ul className="grid gap-2 text-xs sm:grid-cols-2">
                {[
                  "Submit or upload to Moodle",
                  "Change grades or profile",
                  "Delete course content",
                  "Bypass SSO, MFA or CAPTCHA",
                  "Execute generated code",
                  "Run shell commands",
                  "Talk to cloud AI",
                  "Accept outside connections",
                ].map((item) => (
                  <li key={item} className="flex items-start gap-2 rounded-md border bg-muted/20 px-2.5 py-2">
                    <span className="mt-1.5 size-1 shrink-0 rounded-full bg-muted-foreground/60" />
                    {item}
                  </li>
                ))}
              </ul>
            </CardContent>
          </Card>
        </section>
      </div>
    </div>
  );
}
