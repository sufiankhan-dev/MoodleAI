"use client";

import Link from "next/link";
import { ArrowUpRight, BookOpen, GraduationCap } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { useDashboard } from "@/components/dashboard-provider";
import { cn, initials, timeAgo } from "@/lib/utils";

export function CoursesGrid({ className }: { className?: string }) {
  const { courses } = useDashboard();

  return (
    <Card className={cn("gap-0", className)}>
      <CardHeader className="flex-row items-center justify-between pb-4">
        <div className="space-y-1">
          <CardTitle className="flex items-center gap-2 text-sm">
            <GraduationCap className="text-muted-foreground size-4" />
            Courses
          </CardTitle>
          <CardDescription className="text-xs">
            Saved from your last reads. Click one to ask the agent to open it.
          </CardDescription>
        </div>
        <Badge variant="secondary">{courses.length}</Badge>
      </CardHeader>

      <CardContent>
        {courses.length === 0 ? (
          <div className="flex flex-col items-center justify-center rounded-lg border border-dashed px-4 py-10 text-center">
            <BookOpen className="text-muted-foreground/60 mb-2 size-6" />
            <p className="text-sm font-medium">No courses saved yet</p>
            <p className="text-muted-foreground mt-1 max-w-xs text-xs">
              Ask the agent to “list my courses” once Chrome is logged in.
            </p>
            <Link
              href="/chat?prompt=List%20my%20courses"
              className="text-foreground mt-3 text-xs font-medium underline underline-offset-4"
            >
              Open chat with that prompt
            </Link>
          </div>
        ) : (
          <ul className="grid gap-2 sm:grid-cols-2">
            {courses.map((course) => (
              <li key={course.id}>
                <Link
                  href={`/chat?prompt=${encodeURIComponent(`Open my "${course.name}" course`)}`}
                  className="group hover:bg-accent/60 flex items-center gap-3 rounded-lg border p-3 transition-colors"
                  title={`Ask the agent to open ${course.name}`}
                >
                  <span className="bg-primary/8 text-primary flex size-9 shrink-0 items-center justify-center rounded-md border text-xs font-semibold">
                    {initials(course.name)}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-medium">{course.name}</span>
                    <span className="text-muted-foreground mt-0.5 flex items-center gap-1.5 text-[0.68rem]">
                      <Badge variant="outline" className="h-4 px-1.5 text-[0.6rem] font-normal">
                        {course.moodle_id ? `Moodle #${course.moodle_id}` : "Course"}
                      </Badge>
                      {course.last_seen_at ? <span>{timeAgo(course.last_seen_at)}</span> : null}
                    </span>
                  </span>
                  <ArrowUpRight className="text-muted-foreground/50 group-hover:text-foreground size-4 shrink-0 transition-colors" />
                </Link>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
