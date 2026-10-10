const styles: Record<string, string> = {
  active: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300",
  ok: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300",
  quarantined: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
  degraded: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
  revoked: "bg-zinc-200 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300",
  unreachable: "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300",
  // incident severity / status
  low: "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300",
  medium: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
  high: "bg-orange-100 text-orange-800 dark:bg-orange-950 dark:text-orange-300",
  critical: "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300",
  open: "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300",
  acknowledged: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
  resolved: "bg-zinc-200 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300",
  // model stage
  available: "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300",
  shadow: "bg-sky-100 text-sky-800 dark:bg-sky-950 dark:text-sky-300",
  syncing: "bg-sky-100 text-sky-800 dark:bg-sky-950 dark:text-sky-300",
};

export function StatusBadge({ status }: { status: string }) {
  return (
    <span
      className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${styles[status] ?? styles.revoked}`}
    >
      {status}
    </span>
  );
}

export function TrustScore({ value }: { value: number }) {
  const color =
    value >= 70
      ? "text-emerald-700 dark:text-emerald-400"
      : value >= 40
        ? "text-amber-700 dark:text-amber-400"
        : "text-red-700 dark:text-red-400";
  return <span className={`font-mono tabular-nums ${color}`}>{value.toFixed(0)}</span>;
}
