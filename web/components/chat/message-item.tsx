"use client";

import * as React from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  AlertCircle,
  ChevronRight,
  Circle,
  Loader2,
  Wrench,
  XCircle,
} from "lucide-react";

import type { ChatEntry } from "@/lib/types";
import { cn } from "@/lib/utils";

const REMARK_PLUGINS = [remarkGfm];

const MARKDOWN_COMPONENTS = {
  a: ({ href, children }: { href?: string; children?: React.ReactNode }) => (
    <a href={href} target="_blank" rel="noreferrer noopener">
      {children}
    </a>
  ),
};

export function MessageItem({ entry }: { entry: ChatEntry }) {
  if (entry.kind === "user") {
    return (
      <div className="flex justify-end">
        <div className="bg-primary text-primary-foreground max-w-[85%] rounded-2xl rounded-br-sm px-4 py-2.5 text-sm leading-relaxed whitespace-pre-wrap">
          {entry.text}
        </div>
      </div>
    );
  }

  if (entry.kind === "assistant") {
    return (
      <div className="flex gap-3">
        <div className="bg-primary text-primary-foreground mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-md text-[0.65rem] font-semibold">
          M
        </div>
        <div className="min-w-0 flex-1">
          <div
            className={cn(
              "prose-chat max-w-none break-words",
              entry.streaming && !entry.text && "text-muted-foreground",
            )}
          >
            {entry.text ? (
              <ReactMarkdown remarkPlugins={REMARK_PLUGINS} components={MARKDOWN_COMPONENTS}>
                {entry.text}
              </ReactMarkdown>
            ) : (
              <span className="inline-flex items-center gap-2 text-xs">
                <Loader2 className="size-3 animate-spin" />
                Thinking…
              </span>
            )}
            {entry.streaming && entry.text ? <span className="caret" /> : null}
          </div>
          {entry.meta ? (
            <p className="text-muted-foreground/70 mt-2 text-[0.65rem]">{entry.meta}</p>
          ) : null}
        </div>
      </div>
    );
  }

  if (entry.kind === "error") {
    return (
      <div className="flex gap-3">
        <div className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-md border border-red-500/30 bg-red-500/10 text-red-600 dark:text-red-400">
          <AlertCircle className="size-4" />
        </div>
        <div className="min-w-0 flex-1 rounded-lg border border-red-500/30 bg-red-500/5 px-3 py-2 text-sm leading-relaxed text-red-700 dark:text-red-300">
          {entry.text}
        </div>
      </div>
    );
  }

  if (entry.kind === "notice") {
    return (
      <p className="text-muted-foreground py-1 text-center text-[0.7rem]">{entry.text}</p>
    );
  }

  return <ToolRow entry={entry} />;
}

function ToolRow({
  entry,
}: {
  entry: Extract<ChatEntry, { kind: "tool" }>;
}) {
  const [open, setOpen] = React.useState(false);
  const running = entry.status === "running";
  const failed = entry.status === "error";

  return (
    <div className="ml-10">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        className="hover:bg-accent/60 flex w-full items-center gap-2 rounded-md border bg-muted/25 px-2.5 py-1.5 text-left text-[0.7rem] transition-colors"
      >
        <ChevronRight
          className={cn("text-muted-foreground size-3 shrink-0 transition-transform", open && "rotate-90")}
        />
        {running ? (
          <Loader2 className="size-3.5 shrink-0 animate-spin text-blue-500" />
        ) : failed ? (
          <XCircle className="size-3.5 shrink-0 text-red-500" />
        ) : (
          <Circle className="size-3.5 shrink-0 fill-emerald-500/20 text-emerald-500" />
        )}
        <Wrench className="text-muted-foreground size-3 shrink-0" />
        <span className="font-mono font-medium">{entry.name}</span>
        <span
          className={cn(
            "ml-auto shrink-0 text-[0.62rem]",
            running
              ? "text-blue-600 dark:text-blue-400"
              : failed
                ? "text-red-600 dark:text-red-400"
                : "text-muted-foreground",
          )}
        >
          {running ? "running" : failed ? "failed" : "done"}
        </span>
      </button>
      {open ? (
        <div className="mt-1 space-y-2 rounded-md border bg-muted/20 p-2.5">
          {entry.args ? (
            <div>
              <div className="text-muted-foreground mb-1 text-[0.6rem] font-medium uppercase tracking-wide">
                Arguments
              </div>
              <pre className="pretty-scroll max-h-40 overflow-auto font-mono text-[0.65rem] whitespace-pre-wrap">
                {entry.args}
              </pre>
            </div>
          ) : null}
          {entry.detail ? (
            <div>
              <div className="text-muted-foreground mb-1 text-[0.6rem] font-medium uppercase tracking-wide">
                Result
              </div>
              <p className={cn("text-[0.7rem] leading-relaxed", failed && "text-red-600 dark:text-red-400")}>
                {entry.detail}
              </p>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
