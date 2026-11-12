"use client";

import * as React from "react";
import {
  Activity,
  AlertTriangle,
  BookOpen,
  CheckCircle2,
  Compass,
  Download,
  FileText,
  Globe,
  ShieldCheck,
  Square,
  Trash2,
  User,
  Wrench,
} from "lucide-react";

import { useDashboard } from "@/components/dashboard-provider";
import { Badge } from "@/components/ui/badge";
import type { ActivityItem } from "@/lib/types";
import { cn, formatDay, timeAgo } from "@/lib/utils";

const ICONS: Record<string, React.ComponentType<{ className?: string }>> = {
  browser: Globe,
  navigate: Compass,
  reading: BookOpen,
  download: Download,
  file: FileText,
  error: AlertTriangle,
  warning: AlertTriangle,
  approval_required: ShieldCheck,
  approval_resolved: ShieldCheck,
  approval_timeout: ShieldCheck,
  reset: Trash2,
  stopped: Square,
  status: Activity,
  user_message: User,
  done: CheckCircle2,
  assistant_message: CheckCircle2,
  tool_start: Wrench,
  tool_result: Wrench,
  reset_data: Trash2,
};

const VISIBLE_KINDS = new Set([
  "browser",
  "navigate",
  "reading",
  "download",
  "file",
  "error",
  "warning",
  "approval_required",
  "approval_resolved",
  "approval_timeout",
  "reset",
  "stopped",
  "tool_start",
  "tool_result",
  "done",
]);

export function ActivityFeed({
  className,
  limit = 60,
  filterKinds = true,
}: {
  className?: string;
  limit?: number;
  filterKinds?: boolean;
}) {
  const { activity } = useDashboard();

  const rows = React.useMemo(() => {
    const list = filterKinds
      ? activity.filter((item) => VISIBLE_KINDS.has(item.kind))
      : activity;
    return list.slice(0, limit);
  }, [activity, filterKinds, limit]);

  if (rows.length === 0) {
    return (
      <div className={cn("rounded-lg border border-dashed px-4 py-10 text-center", className)}>
        <Activity className="text-muted-foreground/50 mx-auto mb-2 size-5" />
        <p className="text-sm font-medium">No activity yet</p>
        <p className="text-muted-foreground mt-1 text-xs">
          Agent steps, navigation, downloads and approvals will show up here.
        </p>
      </div>
    );
  }

  return (
    <ol className={cn("relative space-y-0", className)}>
      {rows.map((item, index) => (
        <ActivityRow
          key={`${item.id ?? item.created_at}-${index}`}
          item={item}
          last={index === rows.length - 1}
        />
      ))}
    </ol>
  );
}

function ActivityRow({ item, last }: { item: ActivityItem; last: boolean }) {
  const Icon = ICONS[item.kind] ?? Activity;
  const isError = item.level === "error" || item.kind === "error";

  return (
    <li className="relative flex gap-3 pb-4">
      {!last ? (
        <span className="bg-border absolute top-7 left-[13px] h-[calc(100%-14px)] w-px" />
      ) : null}
      <span
        className={cn(
          "z-10 mt-0.5 flex size-[27px] shrink-0 items-center justify-center rounded-full border",
          isError
            ? "border-red-500/25 bg-red-500/10 text-red-600 dark:text-red-400"
            : "bg-muted text-muted-foreground",
        )}
      >
        <Icon className="size-3.5" />
      </span>
      <div className="min-w-0 flex-1 pt-0.5">
        <p className={cn("text-xs leading-relaxed", isError && "text-red-600 dark:text-red-400")}>
          {item.message}
        </p>
        <div className="mt-1 flex flex-wrap items-center gap-1.5">
          <Badge variant="outline" className="h-4 px-1.5 text-[0.6rem] font-normal lowercase">
            {item.kind.replace(/_/g, " ")}
          </Badge>
          <span className="text-muted-foreground/70 text-[0.62rem]" title={item.created_at}>
            {timeAgo(item.created_at) || formatDay(item.created_at)}
          </span>
        </div>
      </div>
    </li>
  );
}
