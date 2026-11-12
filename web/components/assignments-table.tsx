"use client";

import * as React from "react";
import Link from "next/link";
import { ArrowUpRight, ClipboardList } from "lucide-react";

import { useDashboard } from "@/components/dashboard-provider";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { cn, truncate } from "@/lib/utils";

export function AssignmentsTable({
  className,
  limit,
  showDescription = true,
}: {
  className?: string;
  limit?: number;
  showDescription?: boolean;
}) {
  const { assignments } = useDashboard();

  const rows = React.useMemo(() => {
    const sorted = [...assignments].sort(
      (a, b) => Number(Boolean(b.pending)) - Number(Boolean(a.pending)),
    );
    return typeof limit === "number" ? sorted.slice(0, limit) : sorted;
  }, [assignments, limit]);

  const pending = assignments.filter((row) => row.pending).length;

  return (
    <Card className={cn("gap-0", className)}>
      <CardHeader className="flex-row items-center justify-between pb-4">
        <div className="space-y-1">
          <CardTitle className="flex items-center gap-2 text-sm">
            <ClipboardList className="text-muted-foreground size-4" />
            Assignments
          </CardTitle>
          {showDescription ? (
            <CardDescription className="text-xs">
              {pending} pending · {assignments.length} saved
            </CardDescription>
          ) : null}
        </div>
        <Badge variant={pending > 0 ? "warning" : "secondary"}>{pending} pending</Badge>
      </CardHeader>

      <CardContent className="px-0">
        {rows.length === 0 ? (
          <div className="mx-5 rounded-lg border border-dashed px-4 py-10 text-center">
            <p className="text-sm font-medium">No assignments saved yet</p>
            <p className="text-muted-foreground mt-1 text-xs">
              Open a course first, then ask for pending assignments.
            </p>
          </div>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="pl-5">Assignment</TableHead>
                <TableHead>Course</TableHead>
                <TableHead>Due</TableHead>
                <TableHead className="pr-5 text-right">Status</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((row) => {
                const prompt = `Open assignment "${row.name}"${
                  row.course_name ? ` in "${row.course_name}"` : ""
                }`;
                return (
                  <TableRow key={row.id} className="group">
                    <TableCell className="max-w-[22rem] pl-5 whitespace-normal">
                      <Link
                        href={`/chat?prompt=${encodeURIComponent(prompt)}`}
                        className="flex items-start gap-2"
                      >
                        <span className="truncate font-medium">{row.name}</span>
                        <ArrowUpRight className="text-muted-foreground/40 group-hover:text-foreground mt-0.5 size-3.5 shrink-0 transition-colors" />
                      </Link>
                    </TableCell>
                    <TableCell className="text-muted-foreground max-w-[12rem] truncate text-xs">
                      {row.course_name || "—"}
                    </TableCell>
                    <TableCell className="text-muted-foreground text-xs" title={row.due_text || ""}>
                      {row.due_text ? truncate(row.due_text, 34) : "—"}
                    </TableCell>
                    <TableCell className="pr-5 text-right">
                      {row.pending ? (
                        <Badge variant="warning" title={row.status_text || "Pending"}>
                          {row.status_text ? truncate(row.status_text, 26) : "Pending"}
                        </Badge>
                      ) : (
                        <Badge variant="success" title={row.status_text || "Submitted"}>
                          {row.status_text ? truncate(row.status_text, 26) : "Seen"}
                        </Badge>
                      )}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}
