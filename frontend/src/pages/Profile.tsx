import { useEffect, useState } from "react";
import { Crown, BookOpen, Sparkles, Clock } from "lucide-react";
import { api } from "@/api/client";
import type { StatsResponse } from "@/api/types";

export function Profile() {
  const [stats, setStats] = useState<StatsResponse | null>(null);

  useEffect(() => {
    api.getStats().then(setStats).catch(() => undefined);
  }, []);

  return (
    <div className="max-w-3xl">
      <h1 className="mb-10 font-serif text-3xl tracking-wide text-warm-100">
        个人中心
      </h1>

      <section className="card-warm relative overflow-hidden p-8 shadow-card-warm">
        <div className="pointer-events-none absolute inset-0 bg-hero-glow opacity-50" />
        <div className="relative flex items-center gap-6">
          <div className="flex h-20 w-20 items-center justify-center rounded-full bg-gradient-to-br from-gold-300 to-gold-600 font-serif text-3xl font-medium text-base-900 ring-4 ring-gold-400/30">
            墨
          </div>
          <div className="flex-1">
            <div className="flex items-center gap-3">
              <h2 className="font-serif text-2xl tracking-wide text-warm-100">
                墨语者
              </h2>
              <span className="badge-gold">
                <Crown className="mr-1 inline h-3 w-3" strokeWidth={2} />
                VIP
              </span>
            </div>
            <p className="mt-2 text-xs tracking-widest text-warm-400">
              在碎片的世界里，把想想写成书
            </p>
          </div>
        </div>
      </section>

      <section className="mt-8 grid grid-cols-1 gap-4 sm:grid-cols-3">
        <div className="card-warm p-6">
          <BookOpen className="h-5 w-5 text-gold-300" strokeWidth={1.5} />
          <div className="mt-4 stat-number leading-none">
            {stats?.total_books ?? "—"}
          </div>
          <div className="mt-2 text-[11px] tracking-widest text-warm-400">
            创作书稿
          </div>
        </div>
        <div className="card-warm p-6">
          <Sparkles className="h-5 w-5 text-gold-300" strokeWidth={1.5} />
          <div className="mt-4 stat-number leading-none">
            {stats
              ? stats.total_words >= 10000
                ? `${(stats.total_words / 10000).toFixed(1)}万`
                : stats.total_words
              : "—"}
          </div>
          <div className="mt-2 text-[11px] tracking-widest text-warm-400">
            累计字数
          </div>
        </div>
        <div className="card-warm p-6">
          <Clock className="h-5 w-5 text-gold-300" strokeWidth={1.5} />
          <div className="mt-4 stat-number leading-none">
            {stats ? `${stats.saved_hours}h` : "—"}
          </div>
          <div className="mt-2 text-[11px] tracking-widest text-warm-400">
            节省时间
          </div>
        </div>
      </section>

      <section className="card-warm mt-8 p-8">
        <h3 className="font-serif text-lg tracking-wide text-warm-100">
          账号设置
        </h3>
        <p className="mt-2 text-xs tracking-wide text-warm-400">
          账号管理、通知偏好、API Key 管理等功能即将上线
        </p>
      </section>
    </div>
  );
}
