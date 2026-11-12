"use client";

import { Badge } from "@/components/ui/badge";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { useDashboard } from "@/components/dashboard-provider";
import { cn } from "@/lib/utils";
import type { BrowserState } from "@/lib/types";

const BROWSER_LABEL: Record<BrowserState, { label: string; variant: "success" | "warning" | "destructive" | "muted" }> = {
  disconnected: { label: "Browser off", variant: "muted" },
  starting: { label: "Starting", variant: "warning" },
  login_required: { label: "Login needed", variant: "warning" },
  ready: { label: "Moodle ready", variant: "success" },
  unknown: { label: "Browser open", variant: "warning" },
  error: { label: "Browser error", variant: "destructive" },
};

export function StatusBadges() {
  const { ollama, browser, agent, running, connected } = useDashboard();

  const browserState = (browser?.state ?? "disconnected") as BrowserState;
  const browserInfo = BROWSER_LABEL[browserState] ?? BROWSER_LABEL.unknown;

  const ollamaVariant = !ollama
    ? "muted"
    : !ollama.reachable
      ? "destructive"
      : !ollama.installed
        ? "warning"
        : "success";

  const ollamaLabel = !ollama
    ? "Ollama"
    : !ollama.reachable
      ? "Ollama offline"
      : !ollama.installed
        ? `${ollama.model} missing`
        : ollama.model;

  const agentLabel = running
    ? agent.status === "waiting_approval"
      ? "Waiting for you"
      : agent.status === "stopping"
        ? "Stopping"
        : "Working"
    : "Idle";

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <Tooltip>
        <TooltipTrigger asChild>
          <Badge variant={ollamaVariant} className="cursor-default gap-1.5">
            <span
              className={cn(
                "size-1.5 rounded-full",
                ollamaVariant === "success"
                  ? "bg-emerald-500"
                  : ollamaVariant === "warning"
                    ? "bg-amber-500"
                    : ollamaVariant === "destructive"
                      ? "bg-red-500"
                      : "bg-muted-foreground/60",
              )}
            />
            {ollamaLabel}
          </Badge>
        </TooltipTrigger>
        <TooltipContent>
          {ollama?.reachable
            ? `Local Ollama ready${ollama.latency_ms ? ` · ${ollama.latency_ms} ms` : ""}`
            : (ollama?.error ?? "Waiting for the local model")}
        </TooltipContent>
      </Tooltip>

      <Tooltip>
        <TooltipTrigger asChild>
          <Badge variant={browserInfo.variant} className="cursor-default gap-1.5">
            <span
              className={cn(
                "size-1.5 rounded-full",
                browserInfo.variant === "success"
                  ? "bg-emerald-500"
                  : browserInfo.variant === "warning"
                    ? "bg-amber-500"
                    : browserInfo.variant === "destructive"
                      ? "bg-red-500"
                      : "bg-muted-foreground/60",
              )}
            />
            {browserInfo.label}
          </Badge>
        </TooltipTrigger>
        <TooltipContent>{browser?.error ?? browserInfo.label}</TooltipContent>
      </Tooltip>

      <Tooltip>
        <TooltipTrigger asChild>
          <Badge
            variant={running ? "default" : "outline"}
            className="cursor-default gap-1.5"
          >
            <span
              className={cn(
                "size-1.5 rounded-full",
                running ? "animate-pulse bg-emerald-400" : "bg-muted-foreground/50",
              )}
            />
            {agentLabel}
          </Badge>
        </TooltipTrigger>
        <TooltipContent>
          {connected ? "Live updates connected" : "Reconnecting to live updates…"}
        </TooltipContent>
      </Tooltip>
    </div>
  );
}
