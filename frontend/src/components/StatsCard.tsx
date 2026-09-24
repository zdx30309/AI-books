import { BookOpen, Clock, Sparkles } from "lucide-react";
import type { StatsResponse } from "@/api/types";

interface Props {
  stats: StatsResponse | null;
  loading: boolean;
}

function formatWords(n: number): { value: string; unit: string } {
  if (n >= 100_000_000) return { value: (n / 100_000_000).toFixed(2), unit: "亿字" };
  if (n >= 10_000) return { value: (n / 10_000).toFixed(1), unit: "万字" };
  return { value: String(n), unit: "字" };
}

function StatItem({
  icon: Icon,
  value,
  unit,
  label,
  loading,
}: {
  icon: typeof BookOpen;
  value: string;
  unit: string;
  label: string;
  loading: boolean;
}) {
  return (
    <div className="surface-muted flex flex-col justify-between gap-5 p-4 sm:p-5">
      <div className="flex items-center gap-2 text-[11.5px] text-warm-400">
        <Icon className="h-3.5 w-3.5 text-gold-300" strokeWidth={1.8} />
        {label}
      </div>
      {loading ? (
        <div className="skeleton h-8 w-20 rounded-md" />
      ) : (
        <div className="flex items-baseline gap-1">
          <span className="stat-number leading-none">{value}</span>
          <span className="text-xs text-warm-400">{unit}</span>
        </div>
      )}
    </div>
  );
}

export function StatsCard({ stats, loading }: Props) {
  const words = formatWords(stats?.total_words ?? 0);
  return (
    <div className="grid grid-cols-3 gap-2.5 sm:gap-3">
      <StatItem icon={BookOpen} value={String(stats?.total_books ?? 0)} unit="本" label="已创作书稿" loading={loading} />
      <StatItem icon={Sparkles} value={words.value} unit={words.unit} label="字数累计" loading={loading} />
      <StatItem icon={Clock} value={String(stats?.saved_hours ?? 0)} unit="小时" label="节省时间" loading={loading} />
    </div>
  );
}
