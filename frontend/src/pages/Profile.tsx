import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Crown, BookOpen, Sparkles, Clock, LogOut, Mail, Phone, Calendar } from "lucide-react";
import { useAuth } from "@/auth/AuthContext";
import { api } from "@/api/client";
import type { StatsResponse } from "@/api/types";

function formatJoined(iso: string): string {
  try {
    const d = new Date(iso);
    return `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日`;
  } catch {
    return iso;
  }
}

export function Profile() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [stats, setStats] = useState<StatsResponse | null>(null);

  useEffect(() => {
    api
      .getStats()
      .then(setStats)
      .catch(() => undefined);
  }, []);

  if (!user) return null;

  const avatarChar = (user.name || "我").trim().charAt(0);
  const onLogout = () => {
    logout();
    navigate("/login", { replace: true });
  };

  return (
    <div className="max-w-3xl">
      <h1 className="mb-10 font-serif text-3xl tracking-wide text-warm-100">
        个人中心
      </h1>

      {/* 主卡：头像 + 基本信息 */}
      <section className="card-warm relative overflow-hidden p-8 shadow-card-warm">
        <div className="pointer-events-none absolute inset-0 bg-hero-glow opacity-50" />
        <div className="relative flex items-start gap-6">
          <div className="flex h-20 w-20 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-gold-300 to-gold-600 font-serif text-3xl font-medium text-base-900 ring-4 ring-gold-400/30">
            {avatarChar}
          </div>
          <div className="flex-1">
            <div className="flex flex-wrap items-center gap-3">
              <h2 className="font-serif text-2xl tracking-wide text-warm-100">
                {user.name}
              </h2>
              {user.is_vip && (
                <span className="badge-gold">
                  <Crown className="mr-1 inline h-3 w-3" strokeWidth={2} />
                  VIP
                </span>
              )}
            </div>
            <ul className="mt-4 space-y-1.5 text-xs tracking-wide text-warm-300">
              {user.email && (
                <li className="flex items-center gap-2">
                  <Mail className="h-3.5 w-3.5 text-warm-400" strokeWidth={1.5} />
                  <span>{user.email}</span>
                </li>
              )}
              {user.phone && (
                <li className="flex items-center gap-2">
                  <Phone className="h-3.5 w-3.5 text-warm-400" strokeWidth={1.5} />
                  <span>{user.phone}</span>
                </li>
              )}
              <li className="flex items-center gap-2">
                <Calendar className="h-3.5 w-3.5 text-warm-400" strokeWidth={1.5} />
                <span>加入于 {formatJoined(user.created_at)}</span>
              </li>
            </ul>
          </div>
          <button
            type="button"
            onClick={onLogout}
            className="btn-ghost"
            title="退出登录"
          >
            <LogOut className="h-3.5 w-3.5" strokeWidth={1.5} />
            退出登录
          </button>
        </div>
      </section>

      {/* 统计 3 张 */}
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

      {/* 占位：账号设置 */}
      <section className="card-warm mt-8 p-8">
        <h3 className="font-serif text-lg tracking-wide text-warm-100">
          账号设置
        </h3>
        <p className="mt-2 text-xs tracking-wide text-warm-400">
          修改昵称、头像、密码、绑定第三方登录等功能即将上线
        </p>
      </section>
    </div>
  );
}
