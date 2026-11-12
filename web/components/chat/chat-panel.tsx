"use client";

import * as React from "react";
import {
  ArrowDown,
  History,
  Loader2,
  MessageSquarePlus,
  Send,
  Sparkles,
  Square,
  Trash2,
} from "lucide-react";
import { toast } from "sonner";

import { ActivityFeed } from "@/components/activity-feed";
import { useDashboard } from "@/components/dashboard-provider";
import { MessageItem } from "@/components/chat/message-item";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Separator } from "@/components/ui/separator";
import { Textarea } from "@/components/ui/textarea";
import { timeAgo, truncate } from "@/lib/utils";

const SUGGESTIONS = [
  "List my courses",
  "Show me all pending assignments",
  "Open my Database Systems course",
  "Explain my latest assignment in simple language",
];

export function ChatPanel() {
  const {
    entries,
    streaming,
    running,
    sendMessage,
    stopAgent,
    newChat,
    conversationId,
    conversations,
    loadConversation,
    browser,
    approvals,
    activity,
    assignments,
    resetData,
    offline,
  } = useDashboard();

  const [text, setText] = React.useState("");
  const [historyOpen, setHistoryOpen] = React.useState(false);
  const [resetOpen, setResetOpen] = React.useState(false);
  const [showJump, setShowJump] = React.useState(false);
  const scrollRef = React.useRef<HTMLDivElement | null>(null);
  const pinned = React.useRef(true);

  const firstUser = entries.find((entry) => entry.kind === "user");
  const title =
    firstUser && firstUser.kind === "user" ? truncate(firstUser.text, 64) : "New conversation";

  React.useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const prompt = params.get("prompt");
    if (prompt) {
      setText(prompt);
      window.history.replaceState(null, "", window.location.pathname);
    }
  }, []);

  React.useEffect(() => {
    const node = scrollRef.current;
    if (!node) return;
    if (pinned.current) {
      node.scrollTop = node.scrollHeight;
      setShowJump(false);
    } else {
      setShowJump(true);
    }
  }, [entries, streaming]);

  function onScroll() {
    const node = scrollRef.current;
    if (!node) return;
    const distance = node.scrollHeight - node.scrollTop - node.clientHeight;
    pinned.current = distance < 80;
    if (pinned.current) setShowJump(false);
  }

  function submit(raw?: string) {
    const value = (raw ?? text).trim();
    if (!value) return;
    if (value.toLowerCase() === "/reset") {
      setResetOpen(true);
      return;
    }
    if (running) {
      toast.warning("The agent is already working", {
        description: "Stop it or wait for the current turn to finish.",
      });
      return;
    }
    setText("");
    pinned.current = true;
    void sendMessage(value);
  }

  const pendingAssignments = assignments.filter((row) => row.pending).length;

  return (
    <div className="flex h-full min-h-0">
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex h-14 shrink-0 items-center gap-2 border-b px-4">
          <div className="min-w-0 flex-1">
            <h2 className="truncate text-sm font-semibold" title={title}>
              {title}
            </h2>
            <p className="text-muted-foreground truncate text-[0.65rem]">
              {conversationId ? `Conversation #${conversationId}` : "Local conversation"} ·{" "}
              qwen3:8b
            </p>
          </div>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setHistoryOpen(true)}
            title="Conversation history"
          >
            <History className="size-3.5" />
            <span className="hidden sm:inline">History</span>
          </Button>
          <Button variant="outline" size="sm" onClick={newChat}>
            <MessageSquarePlus className="size-3.5" />
            <span className="hidden sm:inline">New chat</span>
          </Button>
        </div>

        <div className="relative min-h-0 flex-1">
          <div
            ref={scrollRef}
            onScroll={onScroll}
            className="pretty-scroll h-full overflow-y-auto px-4 py-5 lg:px-8"
          >
            <div className="mx-auto flex max-w-3xl flex-col gap-5">
              {entries.length === 0 ? (
                <Welcome onPick={(prompt) => submit(prompt)} />
              ) : (
                entries.map((entry) => <MessageItem key={entry.id} entry={entry} />)
              )}
            </div>
          </div>

          {showJump ? (
            <div className="pointer-events-none absolute inset-x-0 bottom-3 flex justify-center">
              <Button
                size="sm"
                variant="secondary"
                className="pointer-events-auto shadow-md"
                onClick={() => {
                  const node = scrollRef.current;
                  if (node) node.scrollTop = node.scrollHeight;
                  pinned.current = true;
                  setShowJump(false);
                }}
              >
                <ArrowDown className="size-3.5" />
                Jump to latest
              </Button>
            </div>
          ) : null}
        </div>

        <div className="shrink-0 border-t bg-background px-4 py-3 lg:px-8">
          <div className="mx-auto max-w-3xl">
            <div className="focus-within:border-ring/60 flex items-end gap-2 rounded-xl border bg-card p-2 shadow-sm transition-colors">
              <Textarea
                value={text}
                onChange={(event) => setText(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    submit();
                  }
                }}
                placeholder="Ask MoodleAI…  (Shift+Enter for a new line)"
                aria-label="Message"
                rows={1}
                disabled={offline}
                className="max-h-40 min-h-9 border-0 bg-transparent px-1.5 py-2 shadow-none focus-visible:ring-0"
              />
              {running ? (
                <Button
                  type="button"
                  variant="destructive"
                  size="icon"
                  aria-label="Stop agent"
                  onClick={() => void stopAgent()}
                >
                  <Square className="size-4" />
                </Button>
              ) : (
                <Button
                  type="button"
                  size="icon"
                  aria-label="Send message"
                  disabled={!text.trim() || offline}
                  onClick={() => submit()}
                >
                  {streaming ? (
                    <Loader2 className="size-4 animate-spin" />
                  ) : (
                    <Send className="size-4" />
                  )}
                </Button>
              )}
            </div>

            <div className="text-muted-foreground mt-2 flex flex-wrap items-center justify-between gap-2 text-[0.65rem]">
              <span>
                {running
                  ? "Working… the answer streams live."
                  : "Everything stays on this machine."}
              </span>
              <span className="flex items-center gap-2">
                <span className="hidden sm:inline">
                  Type <code className="bg-muted rounded px-1 py-0.5">/reset</code> to clear saved
                  data
                </span>
                {text.length > 7000 ? (
                  <span className={text.length > 8000 ? "text-red-500" : ""}>
                    {text.length}/8000
                  </span>
                ) : null}
              </span>
            </div>
          </div>
        </div>
      </div>

      {/* session rail */}
      <aside className="pretty-scroll hidden w-80 shrink-0 overflow-y-auto border-l bg-muted/20 p-4 xl:block">
        <div className="space-y-4">
          <Card className="gap-3 py-4">
            <CardHeader className="px-4 pb-0">
              <CardTitle className="text-xs uppercase tracking-wide text-muted-foreground">
                Current page
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-1 px-4">
              <p className="truncate text-sm font-medium">
                {browser?.page?.title || browser?.title || "No page open"}
              </p>
              <p className="text-muted-foreground truncate font-mono text-[0.65rem]">
                {browser?.page?.url || browser?.url || "Connect Moodle to begin"}
              </p>
              <div className="flex flex-wrap gap-1.5 pt-1">
                <Badge variant="outline" className="text-[0.6rem] uppercase">
                  {(browser?.state ?? "disconnected").replace("_", " ")}
                </Badge>
                {browser?.logged_in ? (
                  <Badge variant="success" className="text-[0.6rem]">
                    logged in
                  </Badge>
                ) : null}
              </div>
            </CardContent>
          </Card>

          {approvals.length > 0 ? (
            <Card className="gap-3 border-amber-500/30 bg-amber-500/5 py-4">
              <CardHeader className="px-4 pb-0">
                <CardTitle className="text-xs uppercase tracking-wide text-amber-700 dark:text-amber-300">
                  Waiting for you
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-2 px-4">
                {approvals.map((approval) => (
                  <p key={approval.approval_id} className="text-xs leading-relaxed">
                    {approval.action}
                  </p>
                ))}
                <p className="text-muted-foreground text-[0.65rem]">
                  Respond in the approval dialog. A timeout denies the action.
                </p>
              </CardContent>
            </Card>
          ) : null}

          <Card className="gap-3 py-4">
            <CardHeader className="flex-row items-center justify-between px-4 pb-0">
              <CardTitle className="text-xs uppercase tracking-wide text-muted-foreground">
                Session
              </CardTitle>
              <Badge variant={pendingAssignments > 0 ? "warning" : "secondary"}>
                {pendingAssignments} pending
              </Badge>
            </CardHeader>
            <CardContent className="space-y-2 px-4 text-xs">
              <div className="flex items-center justify-between">
                <span className="text-muted-foreground">Agent</span>
                <span className="font-medium capitalize">
                  {running ? "working" : "idle"}
                </span>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-muted-foreground">Messages</span>
                <span className="font-medium tabular-nums">{entries.length}</span>
              </div>
              <Separator />
              <div className="text-muted-foreground text-[0.65rem] leading-relaxed">
                Overwrites and workspace refreshes pause for your approval. Nothing is
                submitted to Moodle automatically.
              </div>
            </CardContent>
          </Card>

          <div>
            <div className="mb-2 flex items-center justify-between">
              <h3 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
                Recent activity
              </h3>
              <Sparkles className="text-muted-foreground/50 size-3.5" />
            </div>
            <ActivityFeed limit={6} className="[&_li]:pb-3" />
          </div>
        </div>
      </aside>

      <HistoryDialog
        open={historyOpen}
        onOpenChange={setHistoryOpen}
        conversations={conversations}
        onPick={(id) => {
          setHistoryOpen(false);
          void loadConversation(id);
        }}
      />

      <Dialog open={resetOpen} onOpenChange={setResetOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Trash2 className="size-4" />
              Clear saved data?
            </DialogTitle>
            <DialogDescription>
              This deletes courses, assignments, conversations, activity and every file in
              workspace and generated. Your saved Moodle URL and Chrome login are kept.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setResetOpen(false)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={() => {
                setResetOpen(false);
                void resetData();
              }}
            >
              <Trash2 className="size-4" />
              Clear everything
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function Welcome({ onPick }: { onPick: (prompt: string) => void }) {
  return (
    <div className="flex flex-col items-center py-12 text-center">
      <div className="bg-primary text-primary-foreground mb-4 flex size-12 items-center justify-center rounded-xl text-base font-semibold shadow-sm">
        M
      </div>
      <h2 className="text-lg font-semibold tracking-tight">Your Moodle, with a local brain.</h2>
      <p className="text-muted-foreground mt-2 max-w-md text-sm leading-relaxed">
        I can open your courses in a visible Chrome window, read assignments, explain them,
        and prepare files. Everything runs on your laptop — no cloud AI.
      </p>

      <div className="mt-6 grid w-full max-w-lg gap-2 sm:grid-cols-2">
        {SUGGESTIONS.map((prompt) => (
          <button
            key={prompt}
            type="button"
            onClick={() => onPick(prompt)}
            className="hover:bg-accent/70 rounded-lg border px-3 py-2.5 text-left text-xs font-medium transition-colors"
          >
            {prompt}
          </button>
        ))}
      </div>

      <p className="text-muted-foreground mt-6 text-[0.65rem]">
        Tip: keep one step per message on CPU-only models — “list assignments”, then “open the
        first one”.
      </p>
    </div>
  );
}

function HistoryDialog({
  open,
  onOpenChange,
  conversations,
  onPick,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  conversations: { id: number; title: string; updated_at: string }[];
  onPick: (id: number) => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Conversation history</DialogTitle>
          <DialogDescription>
            Saved locally in SQLite. Choosing one loads its messages into the chat.
          </DialogDescription>
        </DialogHeader>
        {conversations.length === 0 ? (
          <p className="text-muted-foreground rounded-lg border border-dashed px-4 py-8 text-center text-xs">
            No conversations saved yet.
          </p>
        ) : (
          <ul className="pretty-scroll max-h-80 space-y-1 overflow-y-auto">
            {conversations.map((conversation) => (
              <li key={conversation.id}>
                <button
                  type="button"
                  onClick={() => onPick(conversation.id)}
                  className="hover:bg-accent/70 flex w-full items-center justify-between gap-3 rounded-md border px-3 py-2 text-left transition-colors"
                >
                  <span className="min-w-0">
                    <span className="block truncate text-xs font-medium">
                      {conversation.title}
                    </span>
                    <span className="text-muted-foreground text-[0.62rem]">
                      {timeAgo(conversation.updated_at)}
                    </span>
                  </span>
                  <span className="text-muted-foreground font-mono text-[0.6rem]">
                    #{conversation.id}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </DialogContent>
    </Dialog>
  );
}
