"use client";

import * as React from "react";
import { toast } from "sonner";

import { ApiError, apiFetch } from "@/lib/api";
import type {
  ActivityItem,
  AgentState,
  ApprovalMeta,
  Assignment,
  BrowserStatus,
  ChatEntry,
  Conversation,
  Course,
  FileNode,
  OllamaHealth,
  Snapshot,
  StreamEvent,
  ToolInfo,
} from "@/lib/types";

export type ApprovalItem = ApprovalMeta & { receivedAt: number };

interface SendOptions {
  conversationId?: number | null;
}

interface DashboardContextValue {
  ready: boolean;
  connected: boolean;
  offline: boolean;
  ollama: OllamaHealth | null;
  browser: BrowserStatus | null;
  agent: AgentState;
  running: boolean;
  courses: Course[];
  assignments: Assignment[];
  files: { workspace: FileNode[]; generated: FileNode[] };
  activity: ActivityItem[];
  tools: ToolInfo[];
  conversations: Conversation[];
  approvals: ApprovalItem[];
  entries: ChatEntry[];
  conversationId: number | null;
  streaming: boolean;
  pendingAssignments: number;
  sendMessage: (text: string, options?: SendOptions) => Promise<void>;
  stopAgent: () => Promise<void>;
  newChat: () => void;
  loadConversation: (id: number) => Promise<void>;
  resolveApproval: (id: string, approved: boolean) => Promise<void>;
  connectBrowser: (url: string, httpUsername?: string, httpPassword?: string) => Promise<void>;
  disconnectBrowser: () => Promise<void>;
  checkLogin: () => Promise<void>;
  refreshAll: () => Promise<void>;
  refreshData: () => Promise<void>;
  refreshActivity: () => Promise<void>;
  resetData: () => Promise<void>;
}

const DashboardContext = React.createContext<DashboardContextValue | null>(null);

let counter = 0;
function uid(): string {
  counter += 1;
  return `e${Date.now().toString(36)}${counter}`;
}

const EMPTY_FILES = { workspace: [] as FileNode[], generated: [] as FileNode[] };

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : null;
}

function describeArgs(args: unknown): string | null {
  if (args === undefined || args === null) return null;
  if (typeof args === "string") return args;
  try {
    return JSON.stringify(args);
  } catch {
    return String(args);
  }
}

export function DashboardProvider({ children }: { children: React.ReactNode }) {
  const [ready, setReady] = React.useState(false);
  const [connected, setConnected] = React.useState(false);
  const [offline, setOffline] = React.useState(false);
  const [ollama, setOllama] = React.useState<OllamaHealth | null>(null);
  const [browser, setBrowser] = React.useState<BrowserStatus | null>(null);
  const [agent, setAgent] = React.useState<AgentState>({ status: "idle", running: false });
  const [courses, setCourses] = React.useState<Course[]>([]);
  const [assignments, setAssignments] = React.useState<Assignment[]>([]);
  const [files, setFiles] = React.useState(EMPTY_FILES);
  const [activity, setActivity] = React.useState<ActivityItem[]>([]);
  const [tools, setTools] = React.useState<ToolInfo[]>([]);
  const [conversations, setConversations] = React.useState<Conversation[]>([]);
  const [approvals, setApprovals] = React.useState<ApprovalItem[]>([]);
  const [entries, setEntries] = React.useState<ChatEntry[]>([]);
  const [conversationId, setConversationId] = React.useState<number | null>(null);
  const [streaming, setStreaming] = React.useState(false);

  const lastUserText = React.useRef<string | null>(null);
  const dataTimer = React.useRef<ReturnType<typeof setTimeout> | null>(null);
  const activityTimer = React.useRef<ReturnType<typeof setTimeout> | null>(null);

  const applySnapshot = React.useCallback((snapshot: Snapshot) => {
    setOllama(snapshot.ollama ?? null);
    setBrowser(snapshot.browser ?? null);
    setAgent(snapshot.agent ?? { status: "idle", running: false });
    setCourses(Array.isArray(snapshot.courses) ? snapshot.courses : []);
    setAssignments(Array.isArray(snapshot.assignments) ? snapshot.assignments : []);
    setFiles(snapshot.files ?? EMPTY_FILES);
    setActivity(Array.isArray(snapshot.activity) ? snapshot.activity : []);
    setTools(Array.isArray(snapshot.tools) ? snapshot.tools : []);
    setConversations(Array.isArray(snapshot.conversations) ? snapshot.conversations : []);
    setApprovals(
      (snapshot.approvals ?? []).map((meta) => ({ ...meta, receivedAt: Date.now() })),
    );
    setReady(true);
    setOffline(false);
  }, []);

  const refreshAll = React.useCallback(async () => {
    try {
      const snapshot = await apiFetch<Snapshot>("/api/state");
      applySnapshot(snapshot);
    } catch (error) {
      setOffline(true);
      setReady(true);
      if (error instanceof ApiError && error.status === 0) {
        toast.error("Backend unreachable", {
          description: "Start it with `python main.py`, then reload.",
        });
      }
    }
  }, [applySnapshot]);

  const refreshData = React.useCallback(async () => {
    try {
      const [coursesData, assignmentsData, filesData] = await Promise.all([
        apiFetch<{ courses: Course[] }>("/api/courses"),
        apiFetch<{ assignments: Assignment[] }>("/api/assignments"),
        apiFetch<{ workspace: FileNode[]; generated: FileNode[] }>("/api/workspace"),
      ]);
      setCourses(Array.isArray(coursesData.courses) ? coursesData.courses : []);
      setAssignments(
        Array.isArray(assignmentsData.assignments) ? assignmentsData.assignments : [],
      );
      setFiles(filesData ?? EMPTY_FILES);
    } catch {
      /* the SSE stream stays the primary channel */
    }
  }, []);

  const refreshActivity = React.useCallback(async () => {
    try {
      const data = await apiFetch<{ activity: ActivityItem[] }>("/api/activity");
      setActivity(Array.isArray(data.activity) ? data.activity : []);
    } catch {
      /* ignore */
    }
  }, []);

  const refreshBrowser = React.useCallback(async () => {
    try {
      const status = await apiFetch<BrowserStatus>("/api/browser/status");
      setBrowser(status);
    } catch {
      /* ignore */
    }
  }, []);

  const scheduleDataRefresh = React.useCallback(() => {
    if (dataTimer.current) clearTimeout(dataTimer.current);
    dataTimer.current = setTimeout(() => {
      void refreshData();
      void refreshBrowser();
    }, 400);
  }, [refreshBrowser, refreshData]);

  const scheduleActivityRefresh = React.useCallback(() => {
    if (activityTimer.current) clearTimeout(activityTimer.current);
    activityTimer.current = setTimeout(() => void refreshActivity(), 500);
  }, [refreshActivity]);

  // ---------- stream handling ----------

  const addApproval = React.useCallback((meta: ApprovalMeta) => {
    setApprovals((prev) => {
      if (prev.some((item) => item.approval_id === meta.approval_id)) return prev;
      return [...prev, { ...meta, receivedAt: Date.now() }];
    });
    toast.warning("Approval required", { description: meta.action });
  }, []);

  const removeApproval = React.useCallback((id: string) => {
    setApprovals((prev) => prev.filter((item) => item.approval_id !== id));
  }, []);

  const handleEvent = React.useCallback(
    (event: MessageEvent<string>) => {
      let payload: StreamEvent;
      try {
        payload = JSON.parse(event.data) as StreamEvent;
      } catch {
        return;
      }
      if (!payload?.type) return;
      const { type, message, data } = payload;

      switch (type) {
        case "snapshot": {
          const snapshot = asRecord(data);
          if (!snapshot) break;
          if (snapshot.browser) setBrowser(snapshot.browser as BrowserStatus);
          if (snapshot.agent) setAgent(snapshot.agent as AgentState);
          const nextApprovals = (snapshot.approvals as ApprovalMeta[] | undefined) ?? [];
          setApprovals((prev) => {
            const merged = [...prev];
            for (const meta of nextApprovals) {
              if (!merged.some((item) => item.approval_id === meta.approval_id)) {
                merged.push({ ...meta, receivedAt: Date.now() });
              }
            }
            return merged;
          });
          break;
        }

        case "user_message": {
          if (!message) break;
          setEntries((prev) => {
            const last = prev[prev.length - 1];
            if (last?.kind === "user" && last.text === message) return prev;
            if (lastUserText.current === message) return prev;
            return [...prev, { id: uid(), kind: "user", text: message }];
          });
          lastUserText.current = message;
          break;
        }

        case "assistant_delta": {
          if (!message) break;
          setStreaming(true);
          setEntries((prev) => {
            const last = prev[prev.length - 1];
            if (last?.kind === "assistant" && last.streaming) {
              return [...prev.slice(0, -1), { ...last, text: last.text + message }];
            }
            return [...prev, { id: uid(), kind: "assistant", text: message, streaming: true }];
          });
          break;
        }

        case "assistant_message": {
          setStreaming(false);
          setEntries((prev) => {
            const index = prev.map((entry) => entry.kind).lastIndexOf("assistant");
            const last = index >= 0 ? prev[index] : null;
            if (last && last.kind === "assistant" && last.streaming) {
              const next = [...prev];
              next[index] = { ...last, text: message, streaming: false };
              return next;
            }
            return [...prev, { id: uid(), kind: "assistant", text: message }];
          });
          break;
        }

        case "tool_start": {
          const tool = String(data?.tool ?? "tool");
          const args = describeArgs(data?.arguments);
          setEntries((prev) => [
            ...prev,
            { id: uid(), kind: "tool", name: tool, args, status: "running" },
          ]);
          break;
        }

        case "tool_result": {
          setEntries((prev) => {
            for (let index = prev.length - 1; index >= 0; index -= 1) {
              const entry = prev[index];
              if (entry.kind === "tool" && entry.status === "running") {
                const failed = data?.ok === false || Boolean(data?.error);
                const next = [...prev];
                next[index] = {
                  ...entry,
                  status: failed ? "error" : "ok",
                  detail: message || (failed ? String(data?.error ?? "Tool failed") : ""),
                };
                return next;
              }
            }
            return prev;
          });
          scheduleDataRefresh();
          scheduleActivityRefresh();
          break;
        }

        case "status": {
          const status = String(data?.status ?? "idle");
          setAgent((prev) => ({ ...prev, status, running: status !== "idle" }));
          break;
        }

        case "approval_required": {
          const meta = asRecord(data) as ApprovalMeta | null;
          if (meta?.approval_id) addApproval(meta);
          scheduleActivityRefresh();
          break;
        }

        case "approval_resolved":
        case "approval_timeout": {
          const id = String(data?.approval_id ?? "");
          if (id) removeApproval(id);
          if (type === "approval_timeout") {
            toast.error("Approval timed out", {
              description: "The action was denied automatically.",
            });
          }
          break;
        }

        case "browser":
        case "navigate": {
          void refreshBrowser();
          scheduleActivityRefresh();
          break;
        }

        case "file":
        case "download": {
          scheduleDataRefresh();
          scheduleActivityRefresh();
          break;
        }

        case "reading":
        case "warning":
        case "reset":
        case "stopped": {
          scheduleActivityRefresh();
          if (type === "reset") {
            setEntries([]);
            setConversationId(null);
            setApprovals([]);
            setStreaming(false);
            void refreshAll();
          }
          break;
        }

        case "done": {
          setStreaming(false);
          setAgent((prev) => ({ ...prev, status: "idle", running: false }));
          const steps = Number(data?.steps ?? 0);
          const toolCalls = Number(data?.tool_calls ?? 0);
          const meta = [
            steps ? `${steps} model round${steps === 1 ? "" : "s"}` : "",
            toolCalls ? `${toolCalls} tool call${toolCalls === 1 ? "" : "s"}` : "",
          ]
            .filter(Boolean)
            .join(" · ");
          if (meta) {
            setEntries((prev) => {
              for (let index = prev.length - 1; index >= 0; index -= 1) {
                const entry = prev[index];
                if (entry.kind === "assistant") {
                  const next = [...prev];
                  next[index] = { ...entry, streaming: false, meta };
                  return next;
                }
              }
              return prev;
            });
          }
          scheduleDataRefresh();
          scheduleActivityRefresh();
          break;
        }

        case "error": {
          setStreaming(false);
          setAgent((prev) => ({ ...prev, status: "idle", running: false }));
          setEntries((prev) => [
            ...prev,
            { id: uid(), kind: "error", text: message || "Something went wrong." },
          ]);
          scheduleActivityRefresh();
          break;
        }

        default:
          break;
      }
    },
    [
      addApproval,
      refreshAll,
      refreshBrowser,
      removeApproval,
      scheduleActivityRefresh,
      scheduleDataRefresh,
    ],
  );

  React.useEffect(() => {
    let source: EventSource | null = null;
    let retry: ReturnType<typeof setTimeout> | null = null;
    let closed = false;

    const open = () => {
      if (closed) return;
      source = new EventSource("/api/events");
      source.onopen = () => {
        setConnected(true);
        setOffline(false);
      };
      source.onmessage = handleEvent;
      source.onerror = () => {
        setConnected(false);
        if (source) source.close();
        source = null;
        if (!closed) {
          retry = setTimeout(open, 3000);
        }
      };
    };

    open();
    return () => {
      closed = true;
      if (retry) clearTimeout(retry);
      source?.close();
    };
  }, [handleEvent]);

  React.useEffect(() => {
    void refreshAll();
  }, [refreshAll]);

  // ---------- actions ----------

  const sendMessage = React.useCallback(
    async (text: string, options: SendOptions = {}) => {
      const cleaned = text.trim();
      if (!cleaned) return;
      if (cleaned.length > 8000) {
        toast.error("That message is too long", { description: "Maximum 8000 characters." });
        return;
      }

      const activeConversation =
        options.conversationId !== undefined ? options.conversationId : conversationId;
      lastUserText.current = cleaned;
      setEntries((prev) => [...prev, { id: uid(), kind: "user", text: cleaned }]);
      setAgent((prev) => ({ ...prev, status: "running", running: true }));

      try {
        const response = await apiFetch<{ conversation_id: number; run_id: string }>(
          "/api/chat",
          {
            method: "POST",
            body: { message: cleaned, conversation_id: activeConversation },
          },
        );
        setConversationId(response.conversation_id);
      } catch (error) {
        setAgent((prev) => ({ ...prev, status: "idle", running: false }));
        if (error instanceof ApiError && error.status === 409) {
          toast.warning("Agent busy", { description: error.message });
          return;
        }
        setEntries((prev) => [
          ...prev,
          {
            id: uid(),
            kind: "error",
            text: error instanceof Error ? error.message : "Could not send the message.",
          },
        ]);
      }
    },
    [conversationId],
  );

  const stopAgent = React.useCallback(async () => {
    try {
      await apiFetch<{ ok: boolean }>("/api/agent/stop", { method: "POST", body: {} });
      toast.message("Stop requested", {
        description: "The agent stops after the current step.",
      });
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not stop the agent.");
    }
  }, []);

  const newChat = React.useCallback(() => {
    setEntries([]);
    setConversationId(null);
    setStreaming(false);
    lastUserText.current = null;
  }, []);

  const loadConversation = React.useCallback(async (id: number) => {
    try {
      const data = await apiFetch<{
        conversation_id: number;
        messages: { role: string; content: string; created_at: string }[];
      }>(`/api/conversations/${id}/messages`);
      const loaded: ChatEntry[] = (data.messages ?? [])
        .filter((row) => row.content?.trim() && (row.role === "user" || row.role === "assistant"))
        .map((row) => ({
          id: uid(),
          kind: row.role === "user" ? ("user" as const) : ("assistant" as const),
          text: row.content,
        }));
      setEntries(loaded);
      setConversationId(data.conversation_id);
      setStreaming(false);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not load that conversation.");
    }
  }, []);

  const resolveApproval = React.useCallback(
    async (id: string, approved: boolean) => {
      removeApproval(id);
      try {
        await apiFetch(`/api/approvals/${encodeURIComponent(id)}`, {
          method: "POST",
          body: { approved },
        });
      } catch (error) {
        toast.error(error instanceof Error ? error.message : "Could not resolve the approval.");
        void refreshAll();
      }
    },
    [refreshAll, removeApproval],
  );

  const connectBrowser = React.useCallback(
    async (url: string, httpUsername = "", httpPassword = "") => {
      const status = await apiFetch<BrowserStatus>("/api/browser/connect", {
        method: "POST",
        body: { url, http_username: httpUsername, http_password: httpPassword },
      });
      setBrowser(status);
      void refreshAll();
    },
    [refreshAll],
  );

  const disconnectBrowser = React.useCallback(async () => {
    const status = await apiFetch<BrowserStatus>("/api/browser/disconnect", {
      method: "POST",
      body: {},
    });
    setBrowser(status);
  }, []);

  const checkLogin = React.useCallback(async () => {
    try {
      const status = await apiFetch<BrowserStatus>("/api/browser/check-login", {
        method: "POST",
        body: {},
      });
      setBrowser(status);
      toast.message("Login check", { description: status.state.replace("_", " ") });
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not check the login.");
    }
  }, []);

  const resetData = React.useCallback(async () => {
    try {
      await apiFetch("/api/reset", { method: "POST", body: {} });
      setEntries([]);
      setConversationId(null);
      setApprovals([]);
      setStreaming(false);
      toast.success("Saved data cleared", {
        description: "Courses, assignments, chats and files are gone. Login kept.",
      });
      await refreshAll();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not reset saved data.");
    }
  }, [refreshAll]);

  const running = Boolean(agent.running);
  const pendingAssignments = assignments.filter((row) => row.pending).length;

  const value = React.useMemo<DashboardContextValue>(
    () => ({
      ready,
      connected,
      offline,
      ollama,
      browser,
      agent,
      running,
      courses,
      assignments,
      files,
      activity,
      tools,
      conversations,
      approvals,
      entries,
      conversationId,
      streaming,
      pendingAssignments,
      sendMessage,
      stopAgent,
      newChat,
      loadConversation,
      resolveApproval,
      connectBrowser,
      disconnectBrowser,
      checkLogin,
      refreshAll,
      refreshData,
      refreshActivity,
      resetData,
    }),
    [
      activity,
      agent,
      approvals,
      assignments,
      browser,
      checkLogin,
      connectBrowser,
      connected,
      conversationId,
      conversations,
      disconnectBrowser,
      entries,
      files,
      loadConversation,
      newChat,
      offline,
      ollama,
      pendingAssignments,
      ready,
      refreshActivity,
      refreshAll,
      refreshData,
      resetData,
      resolveApproval,
      running,
      sendMessage,
      stopAgent,
      streaming,
      tools,
    ],
  );

  return <DashboardContext.Provider value={value}>{children}</DashboardContext.Provider>;
}

export function useDashboard(): DashboardContextValue {
  const context = React.useContext(DashboardContext);
  if (!context) {
    throw new Error("useDashboard must be used inside DashboardProvider");
  }
  return context;
}
