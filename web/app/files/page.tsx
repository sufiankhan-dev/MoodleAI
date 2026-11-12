"use client";

import * as React from "react";
import { FolderTree, RefreshCw } from "lucide-react";

import { useDashboard } from "@/components/dashboard-provider";
import { FileTree } from "@/components/file-tree";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import type { FileNode } from "@/lib/types";

function countFiles(nodes: FileNode[]): number {
  let total = 0;
  for (const node of nodes) {
    if (node.type === "file") total += 1;
    if (node.type === "dir" && node.children) total += countFiles(node.children);
  }
  return total;
}

export default function FilesPage() {
  const { files, refreshData } = useDashboard();
  const [refreshing, setRefreshing] = React.useState(false);

  async function refresh() {
    setRefreshing(true);
    try {
      await refreshData();
    } finally {
      setRefreshing(false);
    }
  }

  const workspaceCount = countFiles(files.workspace);
  const generatedCount = countFiles(files.generated);

  return (
    <div className="pretty-scroll h-full overflow-y-auto">
      <div className="mx-auto max-w-6xl space-y-4 p-4 lg:p-6">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold tracking-tight">Files</h2>
            <p className="text-muted-foreground text-xs">
              Everything the agent read or created, jailed to workspace and generated.
            </p>
          </div>
          <Button variant="outline" size="sm" onClick={() => void refresh()} disabled={refreshing}>
            <RefreshCw className={refreshing ? "size-3.5 animate-spin" : "size-3.5"} />
            Refresh
          </Button>
        </div>

        <div className="grid gap-3 sm:grid-cols-3">
          <div className="bg-card rounded-xl border p-4">
            <div className="text-muted-foreground text-[0.65rem] uppercase tracking-wide">
              Workspace files
            </div>
            <div className="mt-1 text-2xl font-semibold tabular-nums">{workspaceCount}</div>
            <p className="text-muted-foreground mt-0.5 text-[0.68rem]">
              Course material per assignment
            </p>
          </div>
          <div className="bg-card rounded-xl border p-4">
            <div className="text-muted-foreground text-[0.65rem] uppercase tracking-wide">
              Generated files
            </div>
            <div className="mt-1 text-2xl font-semibold tabular-nums">{generatedCount}</div>
            <p className="text-muted-foreground mt-0.5 text-[0.68rem]">
              Files created without an open assignment
            </p>
          </div>
          <div className="bg-card rounded-xl border p-4">
            <div className="text-muted-foreground text-[0.65rem] uppercase tracking-wide">
              Write policy
            </div>
            <div className="mt-1 flex items-center gap-2">
              <Badge variant="warning">approval required</Badge>
            </div>
            <p className="text-muted-foreground mt-1 text-[0.68rem]">
              Overwrites pause until you approve. Denials leave the original untouched.
            </p>
          </div>
        </div>

        <Card className="gap-0">
          <CardHeader className="pb-3">
            <CardTitle className="flex items-center gap-2 text-sm">
              <FolderTree className="text-muted-foreground size-4" />
              File tree
            </CardTitle>
            <CardDescription className="text-xs">
              Click a file row to reveal its download action.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Tabs defaultValue="workspace">
              <TabsList>
                <TabsTrigger value="workspace">
                  Workspace <span className="text-muted-foreground tabular-nums">({workspaceCount})</span>
                </TabsTrigger>
                <TabsTrigger value="generated">
                  Generated <span className="text-muted-foreground tabular-nums">({generatedCount})</span>
                </TabsTrigger>
              </TabsList>
              <TabsContent value="workspace" className="mt-1">
                <FileTree nodes={files.workspace} className="max-h-[calc(100dvh-24rem)] min-h-56" />
              </TabsContent>
              <TabsContent value="generated" className="mt-1">
                <FileTree nodes={files.generated} className="max-h-[calc(100dvh-24rem)] min-h-56" />
              </TabsContent>
            </Tabs>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
