import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function truncate(value: string, max = 80): string {
  const text = String(value ?? "");
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

export function plural(count: number, one: string, many = `${one}s`): string {
  return `${count} ${count === 1 ? one : many}`;
}

export function formatBytes(bytes?: number | null): string {
  const value = Number(bytes);
  if (!value || value < 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let size = value;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  return `${size < 10 && unit > 0 ? size.toFixed(1) : Math.round(size)} ${units[unit]}`;
}

export function formatClock(value?: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}

export function formatDay(value?: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  const today = new Date();
  const sameDay = date.toDateString() === today.toDateString();
  if (sameDay) return `Today ${formatClock(value)}`;
  return date.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function timeAgo(value?: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const seconds = Math.round((Date.now() - date.getTime()) / 1000);
  if (seconds < 45) return "just now";
  if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`;
  if (seconds < 86_400) return `${Math.round(seconds / 3600)}h ago`;
  return `${Math.round(seconds / 86_400)}d ago`;
}

export function initials(value?: string | null): string {
  const text = String(value ?? "").trim();
  if (!text) return "?";
  return text.charAt(0).toUpperCase();
}

export function fileGlyph(name: string): string {
  const ext = String(name).split(".").pop()?.toLowerCase() ?? "";
  if (["pdf"].includes(ext)) return "PDF";
  if (["doc", "docx", "rtf"].includes(ext)) return "DOC";
  if (["xls", "xlsx", "csv"].includes(ext)) return "XLS";
  if (["png", "jpg", "jpeg", "gif", "webp", "svg"].includes(ext)) return "IMG";
  if (["md", "txt", "yaml", "yml", "json"].includes(ext)) return "TXT";
  if (["py", "js", "ts", "tsx", "jsx", "html", "css", "java", "c", "cpp", "sql"].includes(ext))
    return "CODE";
  return "FILE";
}
