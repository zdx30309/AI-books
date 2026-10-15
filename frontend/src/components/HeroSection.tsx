import { ArrowRight } from "lucide-react";
import { StatsCard } from "./StatsCard";
import type { StatsResponse } from "@/api/types";

interface Props {
  stats: StatsResponse | null;
  statsLoading: boolean;
  onCreate: () => void;
}

export function HeroSection({ stats, statsLoading, onCreate }: Props) {
  return (
    <section className="relative overflow-hidden rounded-3xl border border-gold-500/8 bg-base-800/40 px-12 py-14 backdrop-blur-sm">
      {/* 暖光渐变层 */}
      <div className="pointer-events-none absolute inset-0 bg-hero-glow" />

      {/* 内容 */}
      <div className="relative z-10 max-w-3xl">
        {/* 主标题 + 西文衬底 */}
        <div className="relative">
          <h1 className="hero-title text-5xl leading-tight md:text-[56px]">
            将灵感，写成书
          </h1>
          <span
            aria-hidden="true"
            className="hero-overlay absolute -top-4 left-[58%] text-7xl md:text-8xl"
          >
            Tradition
          </span>
        </div>

        <p className="mt-6 text-base tracking-wide text-warm-300">
          AI 提取视频文稿，一键生成高质量短篇书籍
        </p>

        {/* 统计卡 */}
        <div className="mt-10">
          <StatsCard stats={stats} loading={statsLoading} />
        </div>

        {/* 创作按钮 */}
        <div className="mt-8">
          <button
            type="button"
            onClick={onCreate}
            className="btn-gold"
            aria-label="开始创作"
          >
            <span className="tracking-widest">开始创作</span>
            <ArrowRight className="h-4 w-4" strokeWidth={2} />
          </button>
        </div>
      </div>
    </section>
  );
}
