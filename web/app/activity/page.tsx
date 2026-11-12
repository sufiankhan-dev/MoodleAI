"use client";

import * as React from "react";
import { Activity, Search, ShieldAlert } from "lucide-react";

import { ActivityFeed } from "@/components/activity-feed";
import { useDashboard } from "@/components/dashboard-provider";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";

export default function ActivityPage() {
  const { activity, refreshActivity, tools } = useDashboard();
  const [query, setQuery] = React.useState("");
  const [errorsOnly, setErrorsOnly] = React.useState(false);
  const [refreshing, setRefreshing] = React.useState(false);

  const rows = React.useMemo(() => {
    const needle = query.trim().toLowerCase();
    return activity
      .filter((item) => (errorsOnly ? item.level === "error" || item.kind === "error" : true))
      .filter((item) =>
        needle ? `${item.message} ${item.kind}`.toLowerCase().includes(needle) : true,
      );
  }, [activity, errorsOnly, query]);

  const errors = activity.filter((item) => item.level === "error" || item.kind === "error").length;

  async function refresh() {
    setRefreshing(true);
    try {
      await refreshActivity();
    } finally {
      setRefreshing(false);
    }
  }

  return (
    <div className="pretty-scroll h-full overflow-y-auto">
      <div className="mx-auto max-w-5xl space-y-4 p-4 lg:p-6">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold tracking-tight">Activity</h2>
            <p className="text-muted-foreground text-xs">
              Agent steps, navigation, downloads and approvals from the local log.
            </p>
          </div>
          <div className="flex items-center gap-2">
            <Badge variant="secondary">{activity.length} events</Badge>
            {errors > 0 ? <Badge variant="destructive">{errors} errors</Badge> : null}
            <Button variant="outline" size="sm" onClick={() => void refresh()} disabled={refreshing}>
              Refresh
            </Button>
          </div>
        </div>

        <Card className="gap-0">
          <CardHeader className="pb-3">
            <CardTitle className="flex items-center gap-2 text-sm">
              <Activity className="text-muted-foreground size-4" />
              Local event log
            </CardTitle>
            <CardDescription className="text-xs">
              Newest first · secrets and URL query strings are redacted by the backend
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex flex-wrap items-center gap-2">
              <div className="relative min-w-56 flex-1">
                <Search className="text-muted-foreground absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2" />
                <Input
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder="Filter events…"
                  className="h-8 pl-8 text-xs"
                />
              </div>
              <Button
                variant={errorsOnly ? "destructive" : "outline"}
                size="sm"
                onClick={() => setErrorsOnly((value) => !value)}
              >
                <ShieldAlert className="size-3.5" />
                Errors only
              </Button>
              <Badge variant="outline" className="text-[0.65rem]">
                {tools.length} tools registered
              </Badge>
            </div>

            <ActivityFeed filterKinds={false} limit={300} />
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
