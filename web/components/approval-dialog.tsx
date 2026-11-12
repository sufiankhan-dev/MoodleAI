"use client";

import * as React from "react";
import { AlertTriangle, Loader2, ShieldCheck } from "lucide-react";

import { useDashboard } from "@/components/dashboard-provider";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

export function ApprovalDialog() {
  const { approvals, resolveApproval } = useDashboard();
  const current = approvals[0];
  const [busy, setBusy] = React.useState<"approve" | "deny" | null>(null);
  const [remaining, setRemaining] = React.useState<number | null>(null);

  const timeout = current?.timeout_s ?? 300;
  const receivedAt = current?.receivedAt ?? 0;

  React.useEffect(() => {
    if (!current) {
      setRemaining(null);
      return;
    }
    const tick = () => {
      const elapsed = (Date.now() - receivedAt) / 1000;
      setRemaining(Math.max(0, Math.round(timeout - elapsed)));
    };
    tick();
    const timer = setInterval(tick, 1000);
    return () => clearInterval(timer);
  }, [current, receivedAt, timeout]);

  async function decide(approved: boolean) {
    if (!current) return;
    setBusy(approved ? "approve" : "deny");
    try {
      await resolveApproval(current.approval_id, approved);
    } finally {
      setBusy(null);
    }
  }

  const details = current?.details ? JSON.stringify(current.details, null, 2) : "";

  return (
    <Dialog open={Boolean(current)}>
      <DialogContent
        showCloseButton={false}
        onEscapeKeyDown={(event) => event.preventDefault()}
        onInteractOutside={(event) => event.preventDefault()}
        onOpenAutoFocus={(event) => event.preventDefault()}
        className="sm:max-w-xl"
      >
        <DialogHeader>
          <div className="bg-amber-500/10 text-amber-600 dark:text-amber-400 mb-1 flex size-10 items-center justify-center rounded-full border border-amber-500/25">
            <ShieldCheck className="size-5" />
          </div>
          <DialogTitle className="flex items-center gap-2">
            Approval required
            {approvals.length > 1 ? (
              <Badge variant="secondary">{approvals.length} waiting</Badge>
            ) : null}
          </DialogTitle>
          <DialogDescription>
            {current?.action ?? "The agent wants to perform a consequential action."}
          </DialogDescription>
        </DialogHeader>

        {details ? (
          <pre className="bg-muted/60 pretty-scroll max-h-52 overflow-auto rounded-lg border p-3 font-mono text-[0.7rem] leading-relaxed whitespace-pre-wrap">
            {details}
          </pre>
        ) : null}

        <div className="text-muted-foreground flex items-start gap-2 rounded-lg border bg-muted/30 p-3 text-[0.7rem] leading-relaxed">
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-amber-500" />
          <span>
            The agent is paused until you decide. Nothing is submitted to Moodle
            automatically.{" "}
            {remaining !== null
              ? remaining > 0
                ? `Auto-denies in ${remaining}s.`
                : "Denying now — the timeout elapsed."
              : null}
          </span>
        </div>

        <DialogFooter>
          <Button
            variant="ghost"
            onClick={() => void decide(false)}
            disabled={busy !== null}
          >
            {busy === "deny" ? <Loader2 className="size-4 animate-spin" /> : null}
            Deny
          </Button>
          <Button onClick={() => void decide(true)} disabled={busy !== null}>
            {busy === "approve" ? <Loader2 className="size-4 animate-spin" /> : null}
            Approve
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
