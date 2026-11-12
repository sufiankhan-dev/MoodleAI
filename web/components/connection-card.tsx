"use client";

import * as React from "react";
import { Globe, KeyRound, Link2, Loader2, LogIn, Unplug } from "lucide-react";
import { toast } from "sonner";

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
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { cn, truncate } from "@/lib/utils";

const STATE_COPY: Record<
  string,
  { label: string; variant: "success" | "warning" | "destructive" | "muted"; hint: string }
> = {
  disconnected: {
    label: "disconnected",
    variant: "muted",
    hint: "A visible Chrome window will open. Log in manually — this app never asks for or stores your password.",
  },
  starting: {
    label: "starting…",
    variant: "warning",
    hint: "Launching visible Chrome with your persistent profile…",
  },
  login_required: {
    label: "login required",
    variant: "warning",
    hint: "Chrome is open. Log in to Moodle manually (including SSO/MFA). MoodleAI never sees or stores your password.",
  },
  ready: {
    label: "logged in",
    variant: "success",
    hint: "Logged in. The agent can now read your courses and assignments in the visible window.",
  },
  unknown: {
    label: "check login",
    variant: "warning",
    hint: "Chrome is open. If you are already logged in, the agent will confirm it on the next read.",
  },
  error: {
    label: "error",
    variant: "destructive",
    hint: "The browser could not start. Check Chrome and try again.",
  },
};

export function ConnectionCard({ className }: { className?: string }) {
  const { browser, connectBrowser, checkLogin, disconnectBrowser, offline } = useDashboard();
  const [url, setUrl] = React.useState("");
  const [showNetworkAuth, setShowNetworkAuth] = React.useState(false);
  const [username, setUsername] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [busy, setBusy] = React.useState<"connect" | "login" | "disconnect" | null>(null);

  React.useEffect(() => {
    if (!url && browser?.moodle_url) setUrl(browser.moodle_url);
  }, [browser?.moodle_url, url]);

  const state = browser?.state ?? "disconnected";
  const copy = STATE_COPY[state] ?? STATE_COPY.unknown;
  const page = browser?.page;

  async function handleConnect(event: React.FormEvent) {
    event.preventDefault();
    const target = url.trim();
    if (!target) {
      toast.error("Enter your Moodle URL first.");
      return;
    }
    setBusy("connect");
    try {
      await connectBrowser(target, username.trim(), password);
      setPassword("");
      toast.success("Moodle connection started", {
        description: "Finish the login in the visible Chrome window.",
      });
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not connect to Moodle.");
    } finally {
      setBusy(null);
    }
  }

  async function handleCheckLogin() {
    setBusy("login");
    try {
      await checkLogin();
    } finally {
      setBusy(null);
    }
  }

  async function handleDisconnect() {
    setBusy("disconnect");
    try {
      await disconnectBrowser();
      toast.message("Browser disconnected");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Could not disconnect.");
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card className={cn("gap-0", className)}>
      <CardHeader className="flex-row items-start justify-between gap-3 pb-4">
        <div className="space-y-1">
          <CardTitle className="flex items-center gap-2 text-sm">
            <Globe className="text-muted-foreground size-4" />
            Moodle connection
          </CardTitle>
          <CardDescription className="text-xs">
            Visible Chrome, manual login, no password storage.
          </CardDescription>
        </div>
        <Badge variant={copy.variant} className="mt-0.5 capitalize">
          <span
            className={cn(
              "size-1.5 rounded-full",
              copy.variant === "success"
                ? "bg-emerald-500"
                : copy.variant === "warning"
                  ? "bg-amber-500"
                  : copy.variant === "destructive"
                    ? "bg-red-500"
                    : "bg-muted-foreground/60",
            )}
          />
          {copy.label}
        </Badge>
      </CardHeader>

      <CardContent className="space-y-4">
        <form onSubmit={handleConnect} className="space-y-2">
          <Label htmlFor="moodle-url">Moodle URL</Label>
          <div className="flex gap-2">
            <Input
              id="moodle-url"
              type="url"
              inputMode="url"
              placeholder="https://lms.dsu.edu.pk/"
              autoComplete="off"
              spellCheck={false}
              value={url}
              onChange={(event) => setUrl(event.target.value)}
              disabled={busy !== null || offline}
              className="font-mono text-xs"
            />
            <Button type="submit" disabled={busy !== null || offline} className="shrink-0">
              {busy === "connect" ? <Loader2 className="size-4 animate-spin" /> : <Link2 className="size-4" />}
              Connect
            </Button>
          </div>
        </form>

        <p className="text-muted-foreground text-xs leading-relaxed">{browser?.error || copy.hint}</p>

        <div className="rounded-lg border bg-muted/40 p-3">
          <div className="text-[0.7rem] font-medium tracking-wide text-muted-foreground uppercase">
            Current page
          </div>
          <div className="mt-1 truncate text-sm font-medium">
            {page?.title || browser?.title || "No page open"}
          </div>
          <div className="text-muted-foreground mt-0.5 truncate font-mono text-[0.7rem]" title={page?.url || browser?.url || ""}>
            {truncate(page?.url || browser?.url || "—", 80)}
          </div>
          <div className="mt-3 flex flex-wrap gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => void handleCheckLogin()}
              disabled={busy !== null || offline}
            >
              {busy === "login" ? (
                <Loader2 className="size-3.5 animate-spin" />
              ) : (
                <LogIn className="size-3.5" />
              )}
              Check login
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => void handleDisconnect()}
              disabled={busy !== null || state === "disconnected" || offline}
            >
              {busy === "disconnect" ? (
                <Loader2 className="size-3.5 animate-spin" />
              ) : (
                <Unplug className="size-3.5" />
              )}
              Disconnect
            </Button>
          </div>
        </div>

        <div>
          <button
            type="button"
            className="text-muted-foreground hover:text-foreground flex items-center gap-1.5 text-[0.7rem] font-medium"
            onClick={() => setShowNetworkAuth((value) => !value)}
          >
            <KeyRound className="size-3" />
            Network login (only if Chrome asks)
          </button>
          {showNetworkAuth ? (
            <>
              <p className="text-muted-foreground mt-2 text-[0.7rem] leading-relaxed">
                Some campuses put Moodle behind a username/password popup. These go straight
                to Chrome and stay in memory only.
              </p>
              <div className="mt-2 grid grid-cols-2 gap-2">
                <Input
                  placeholder="Username"
                  autoComplete="off"
                  value={username}
                  onChange={(event) => setUsername(event.target.value)}
                  disabled={busy !== null}
                  className="h-8 text-xs"
                />
                <Input
                  type="password"
                  placeholder="Password"
                  autoComplete="off"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  disabled={busy !== null}
                  className="h-8 text-xs"
                />
              </div>
            </>
          ) : null}
        </div>

        <Separator />
        <p className="text-muted-foreground text-[0.68rem] leading-relaxed">
          Everything binds to 127.0.0.1. The agent never submits, uploads, grades, or deletes
          anything in Moodle.
        </p>
      </CardContent>
    </Card>
  );
}
