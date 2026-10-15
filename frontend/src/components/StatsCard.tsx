import { BookOpen, Clock, Sparkles } from "lucide-react";
import type { StatsResponse } from "@/api/types";

interface Props {
  stats: StatsResponse | null;
  loading: boolean;
}

function formatWords(n: number): string {
  if (n >= 10000) return `${(n / 10000).toFixed(1)}万`;
  return String(n);
}

function StatItem({
  icon: Icon,
  value,
  label,
}: {
  icon: typeof BookOpen;
  value: string;
  label: string;
}) {
  return (
    <div className="flex items-center gap-4">
      <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-gold-500/15 bg-gold-500/8">
        <Icon className="h-4 w-4 text-gold-300" strokeWidth={1.6} />
      </div>
      <div>
        <div className="stat-number leading-none">{value}</div>
        <div className="mt-1.5 text-[11px] tracking-widest text-warm-400">
          {label}
        </div>
      </div>
    </div>
  );
}

export function StatsCard({ stats, loading }: Props) {
  return (
    <div className="card-warm relative inline-flex max-w-fit items-center gap-12 px-9 py-5 shadow-card-warm">
      <StatItem
        icon={BookOpen}
        value={loading ? "—" : String(stats?.total_books ?? 0)}
        label="已创作书稿"
      />
      <div className="h-10 w-px bg-gold-500/15" />
      <StatItem
        icon={Sparkles}
        value={loading ? "—" : formatWords(stats?.total_words ?? 0)}
        label="字数累计"
      />
      <div className="h-10 w-px bg-gold-500/15" />
      <StatItem
        icon={Clock}
        value={loading ? "—" : `${stats?.saved_hours ?? 0}小时`}
        label="节省时间"
      />
    </div>
  );
}
