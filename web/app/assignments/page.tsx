"use client";

import * as React from "react";
import Link from "next/link";
import { ArrowUpRight, ClipboardList, Search } from "lucide-react";

import { useDashboard } from "@/components/dashboard-provider";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { cn, truncate } from "@/lib/utils";

export default function AssignmentsPage() {
  const { assignments, courses, refreshData } = useDashboard();
  const [query, setQuery] = React.useState("");
  const [pendingOnly, setPendingOnly] = React.useState(false);
  const [courseId, setCourseId] = React.useState<string>("all");
  const [refreshing, setRefreshing] = React.useState(false);

  const rows = React.useMemo(() => {
    const needle = query.trim().toLowerCase();
    return assignments
      .filter((row) => (pendingOnly ? row.pending : true))
      .filter((row) => (courseId === "all" ? true : String(row.course_id ?? "") === courseId))
      .filter((row) =>
        needle
          ? `${row.name} ${row.course_name ?? ""} ${row.due_text ?? ""} ${row.status_text ?? ""}`
              .toLowerCase()
              .includes(needle)
          : true,
      )
      .sort((a, b) => Number(Boolean(b.pending)) - Number(Boolean(a.pending)));
  }, [assignments, courseId, pendingOnly, query]);

  const pending = assignments.filter((row) => row.pending).length;

  async function refresh() {
    setRefreshing(true);
    try {
      await refreshData();
    } finally {
      setRefreshing(false);
    }
  }

  return (
    <div className="pretty-scroll h-full overflow-y-auto">
      <div className="mx-auto max-w-6xl space-y-4 p-4 lg:p-6">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold tracking-tight">Assignments</h2>
            <p className="text-muted-foreground text-xs">
              {pending} pending · {assignments.length} saved from Moodle
            </p>
          </div>
          <div className="flex items-center gap-2">
            <Badge variant={pending > 0 ? "warning" : "success"}>{pending} pending</Badge>
            <Button variant="outline" size="sm" onClick={() => void refresh()} disabled={refreshing}>
              Refresh saved data
            </Button>
          </div>
        </div>

        <Card className="gap-0">
          <CardContent className="flex flex-wrap items-center gap-2 px-4 py-3">
            <div className="relative min-w-56 flex-1">
              <Search className="text-muted-foreground absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2" />
              <Input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Search assignments, courses, due text…"
                className="h-8 pl-8 text-xs"
              />
            </div>
            <select
              value={courseId}
              onChange={(event) => setCourseId(event.target.value)}
              className="border-input bg-background h-8 rounded-md border px-2 text-xs shadow-xs outline-none focus-visible:ring-[3px] focus-visible:ring-ring/40"
            >
              <option value="all">All courses</option>
              {courses.map((course) => (
                <option key={course.id} value={String(course.id)}>
                  {course.name}
                </option>
              ))}
            </select>
            <Button
              variant={pendingOnly ? "default" : "outline"}
              size="sm"
              onClick={() => setPendingOnly((value) => !value)}
            >
              Pending only
            </Button>
          </CardContent>
        </Card>

        <Card className="gap-0">
          <CardContent className="px-0">
            {rows.length === 0 ? (
              <div className="flex flex-col items-center justify-center px-4 py-16 text-center">
                <ClipboardList className="text-muted-foreground/40 mb-3 size-7" />
                <p className="text-sm font-medium">
                  {assignments.length === 0 ? "No assignments saved yet" : "Nothing matches those filters"}
                </p>
                <p className="text-muted-foreground mt-1 max-w-sm text-xs">
                  {assignments.length === 0
                    ? "Connect Moodle, open a course, and ask the agent to list the assignments."
                    : "Clear the search or switch back to all courses."}
                </p>
                <Button asChild size="sm" className="mt-4">
                  <Link href="/chat?prompt=Show%20me%20all%20pending%20assignments">
                    Ask for pending assignments
                  </Link>
                </Button>
              </div>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="pl-4">Assignment</TableHead>
                    <TableHead>Course</TableHead>
                    <TableHead>Due</TableHead>
                    <TableHead className="pr-4 text-right">Status</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rows.map((row) => (
                    <TableRow key={row.id} className="group">
                      <TableCell className="max-w-[26rem] pl-4 align-top whitespace-normal">
                        <Link
                          href={`/chat?prompt=${encodeURIComponent(
                            `Open assignment "${row.name}"${
                              row.course_name ? ` in "${row.course_name}"` : ""
                            }`,
                          )}`}
                          className="flex items-start gap-2"
                        >
                          <span className="font-medium leading-snug">{row.name}</span>
                          <ArrowUpRight className="text-muted-foreground/40 group-hover:text-foreground mt-0.5 size-3.5 shrink-0 transition-colors" />
                        </Link>
                        {row.status_text ? (
                          <p className="text-muted-foreground mt-0.5 text-[0.65rem]">
                            {truncate(row.status_text, 90)}
                          </p>
                        ) : null}
                      </TableCell>
                      <TableCell className="text-muted-foreground max-w-[12rem] truncate align-top text-xs">
                        {row.course_name || "—"}
                      </TableCell>
                      <TableCell
                        className={cn("max-w-[12rem] align-top text-xs", row.pending && "text-amber-600 dark:text-amber-400")}
                        title={row.due_text || ""}
                      >
                        {row.due_text ? truncate(row.due_text, 40) : "—"}
                      </TableCell>
                      <TableCell className="pr-4 text-right align-top">
                        {row.pending ? (
                          <Badge variant="warning">Pending</Badge>
                        ) : (
                          <Badge variant="success">Submitted</Badge>
                        )}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
