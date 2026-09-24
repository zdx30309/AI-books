import { ArrowRight, Feather } from "lucide-react";
import { Link } from "react-router-dom";
import { useAuth } from "@/auth/AuthContext";
import { COMMAND_SHORTCUT } from "./Sidebar";
import { StatsCard } from "./StatsCard";
import type { StatsResponse } from "@/api/types";

interface Props {
  stats: StatsResponse | null;
  statsLoading: boolean;
  onCreate: () => void;
}

function greetingFor(date: Date): string {
  const hour = date.getHours();
  if (hour < 5) return "夜深了";
  if (hour < 11) return "早上好";
  if (hour < 13) return "中午好";
  if (hour < 18) return "下午好";
  return "晚上好";
}

export function HeroSection({ stats, statsLoading, onCreate }: Props) {
  const { user } = useAuth();
  const now = new Date();
  const dateLabel = new Intl.DateTimeFormat("zh-CN", { month: "long", day: "numeric", weekday: "short" }).format(now);

  return (
    <section className="card-warm relative overflow-hidden rounded-3xl px-6 py-9 shadow-card-warm sm:px-10 sm:py-11 lg:px-12 lg:py-14">
      <div className="pointer-events-none absolute inset-0 bg-hero-glow" />
      {/* 装饰：书页弧线 */}
      <svg
        aria-hidden="true"
        viewBox="0 0 600 400"
        className="pointer-events-none absolute -right-24 -top-16 hidden h-[460px] w-[680px] text-gold-400/[0.13] lg:block"
        fill="none"
        stroke="currentColor"
      >
        {Array.from({ length: 9 }).map((_, i) => (
          <path key={i} d={`M300 380 C ${240 - i * 26} ${300 - i * 18}, ${140 - i * 22} ${120 - i * 6}, ${40 - i * 20} ${40 + i * 4}`} strokeWidth="1" />
        ))}
        {Array.from({ length: 9 }).map((_, i) => (
          <path key={`r${i}`} d={`M300 380 C ${360 + i * 26} ${300 - i * 18}, ${460 + i * 22} ${120 - i * 6}, ${560 + i * 20} ${40 + i * 4}`} strokeWidth="1" />
        ))}
      </svg>

      <div className="relative grid gap-10 lg:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)] lg:items-end">
        <div>
          <p className="eyebrow">
            {greetingFor(now)}
            {user?.name ? `，${user.name}` : ""} · {dateLabel}
          </p>
          <h1 className="hero-title mt-4 text-[40px] leading-[1.1] sm:text-5xl lg:text-[58px]">
            将灵感，写成书
          </h1>
          <p className="mt-5 max-w-md text-[15px] leading-relaxed text-warm-300">
            粘贴任意视频链接，AI 提取文稿、构思结构、润色成稿，一次生成可交付的完整书籍。
          </p>
          <div className="mt-9 flex flex-wrap items-center gap-3">
            <button type="button" onClick={onCreate} className="btn-gold px-6 py-3 text-[15px]" aria-label="开始创作">
              <Feather className="h-4 w-4" strokeWidth={2} />
              开始创作
              <ArrowRight className="h-4 w-4" strokeWidth={2} />
            </button>
            <Link to="/bookshelf" className="btn-ghost px-5 py-3 text-[15px]">
              打开书架
            </Link>
            <span className="hidden items-center gap-1.5 text-xs text-warm-500 sm:inline-flex">
              <span className="kbd">{COMMAND_SHORTCUT}</span>
              随时跳转任意页面
            </span>
          </div>
        </div>

        <StatsCard stats={stats} loading={statsLoading} />
      </div>
    </section>
  );
}
