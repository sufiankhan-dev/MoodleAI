export type BrowserState =
  | "disconnected"
  | "starting"
  | "login_required"
  | "ready"
  | "unknown"
  | "error";

export interface BrowserStatus {
  state: BrowserState;
  connected?: boolean;
  logged_in?: boolean;
  running?: boolean;
  url?: string;
  title?: string;
  error?: string | null;
  moodle_url?: string;
  page?: { title?: string; url?: string } | null;
}

export interface OllamaHealth {
  reachable: boolean;
  installed: boolean;
  model: string;
  latency_ms?: number;
  error?: string | null;
  running?: string[];
}

export interface AgentState {
  status: string;
  running: boolean;
  run_id?: string | null;
  conversation_id?: number | null;
}

export interface Course {
  id: number;
  name: string;
  url: string;
  moodle_id?: string | null;
  last_seen_at?: string;
}

export interface Assignment {
  id: number;
  course_id?: number | null;
  course_name?: string | null;
  name: string;
  url: string;
  due_text?: string | null;
  status_text?: string | null;
  workspace_path?: string | null;
  pending?: boolean;
  last_opened_at?: string | null;
  created_at?: string;
}

export interface FileNode {
  type: "dir" | "file" | "notice";
  name: string;
  path?: string;
  root?: "workspace" | "generated";
  size?: number;
  download_url?: string;
  children?: FileNode[];
}

export interface ActivityItem {
  id?: number;
  level: "info" | "error" | string;
  kind: string;
  message: string;
  created_at: string;
  data?: unknown;
}

export interface ApprovalMeta {
  approval_id: string;
  action: string;
  details?: Record<string, unknown>;
  timeout_s?: number;
}

export interface ToolInfo {
  name: string;
  description: string;
  read_only: boolean;
  requires_approval: boolean;
}

export interface Conversation {
  id: number;
  title: string;
  updated_at: string;
  created_at?: string;
}

export interface Snapshot {
  ollama: OllamaHealth;
  browser: BrowserStatus;
  moodle_url?: string;
  agent: AgentState;
  courses: Course[];
  assignments: Assignment[];
  tools: ToolInfo[];
  activity: ActivityItem[];
  conversations: Conversation[];
  files: { workspace: FileNode[]; generated: FileNode[] };
  approvals: ApprovalMeta[];
  started_at?: string;
}

export interface StreamEvent {
  type: string;
  message: string;
  data: Record<string, unknown> | null;
  run_id?: string | null;
  ts: string;
}

export type ChatEntry =
  | { id: string; kind: "user"; text: string }
  | {
      id: string;
      kind: "assistant";
      text: string;
      streaming?: boolean;
      meta?: string;
    }
  | { id: string; kind: "error"; text: string }
  | { id: string; kind: "notice"; text: string }
  | {
      id: string;
      kind: "tool";
      name: string;
      args: string | null;
      status: "running" | "ok" | "error";
      detail?: string;
    };
