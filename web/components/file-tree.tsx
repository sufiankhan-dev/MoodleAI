"use client";

import * as React from "react";
import {
  ChevronDown,
  ChevronRight,
  Download,
  File as FileIcon,
  FileText,
  Folder,
  FolderOpen,
} from "lucide-react";

import { buildDownloadUrl } from "@/lib/api";
import type { FileNode } from "@/lib/types";
import { cn, formatBytes, fileGlyph } from "@/lib/utils";

function FileRow({ node, depth }: { node: FileNode; depth: number }) {
  const isDir = node.type === "dir";
  const [open, setOpen] = React.useState(depth < 1);

  if (node.type === "notice") {
    return (
      <div
        className="text-muted-foreground py-1.5 text-[0.7rem] italic"
        style={{ paddingLeft: 8 + depth * 14 }}
      >
        {node.name}
      </div>
    );
  }

  if (isDir) {
    return (
      <div>
        <button
          type="button"
          onClick={() => setOpen((value) => !value)}
          className="hover:bg-accent/60 flex w-full items-center gap-1.5 rounded-md py-1.5 text-left text-xs transition-colors"
          style={{ paddingLeft: 8 + depth * 14, paddingRight: 8 }}
        >
          {open ? (
            <ChevronDown className="text-muted-foreground size-3 shrink-0" />
          ) : (
            <ChevronRight className="text-muted-foreground size-3 shrink-0" />
          )}
          {open ? (
            <FolderOpen className="size-3.5 shrink-0 text-amber-500" />
          ) : (
            <Folder className="size-3.5 shrink-0 text-amber-500" />
          )}
          <span className="truncate font-medium">{node.name}</span>
          <span className="text-muted-foreground ml-auto text-[0.62rem]">
            {node.children?.length ?? 0}
          </span>
        </button>
        {open && node.children?.length
          ? node.children.map((child, index) => (
              <FileRow
                key={`${child.path ?? child.name}-${index}`}
                node={child}
                depth={depth + 1}
              />
            ))
          : null}
      </div>
    );
  }

  const glyph = fileGlyph(node.name);
  const downloadable = Boolean(node.path);

  return (
    <div
      className="hover:bg-accent/60 group flex items-center gap-1.5 rounded-md py-1.5 text-xs transition-colors"
      style={{ paddingLeft: 8 + depth * 14, paddingRight: 8 }}
    >
      <span className="w-3 shrink-0" />
      {glyph === "PDF" ? (
        <FileText className="size-3.5 shrink-0 text-red-500" />
      ) : glyph === "DOC" ? (
        <FileText className="size-3.5 shrink-0 text-blue-500" />
      ) : glyph === "CODE" ? (
        <FileText className="size-3.5 shrink-0 text-emerald-500" />
      ) : (
        <FileIcon className="text-muted-foreground size-3.5 shrink-0" />
      )}
      <span className="min-w-0 flex-1 truncate" title={node.path ?? node.name}>
        {node.name}
      </span>
      <span className="text-muted-foreground/70 text-[0.62rem] tabular-nums">
        {formatBytes(node.size)}
      </span>
      {downloadable ? (
        <a
          href={buildDownloadUrl(node)}
          className="text-muted-foreground hover:text-foreground opacity-0 transition-opacity group-hover:opacity-100"
          title={`Download ${node.name}`}
          aria-label={`Download ${node.name}`}
        >
          <Download className="size-3.5" />
        </a>
      ) : null}
    </div>
  );
}

export function FileTree({
  nodes,
  className,
}: {
  nodes: FileNode[];
  className?: string;
}) {
  if (!nodes.length) {
    return (
      <div className={cn("rounded-lg border border-dashed px-4 py-10 text-center", className)}>
        <Folder className="text-muted-foreground/50 mx-auto mb-2 size-5" />
        <p className="text-sm font-medium">No files yet</p>
        <p className="text-muted-foreground mt-1 text-xs">
          Assignment material and generated study files will appear here.
        </p>
      </div>
    );
  }

  return (
    <div className={cn("pretty-scroll max-h-[26rem] overflow-y-auto pr-1", className)}>
      {nodes.map((node, index) => (
        <FileRow key={`${node.path ?? node.name}-${index}`} node={node} depth={0} />
      ))}
    </div>
  );
}
