import { Link } from "react-router-dom";
import { Crown } from "lucide-react";
import { useAuth } from "@/auth/AuthContext";

export function UserAvatar({ size = 32 }: { size?: number }) {
  const { user } = useAuth();
  if (!user) return null;
  const avatarChar = (user.name || "我").trim().charAt(0);
  return (
    <span
      className="relative inline-flex shrink-0 overflow-hidden rounded-full ring-1 ring-gold-400/35"
      style={{ width: size, height: size }}
    >
      {user.avatar_url ? (
        <img src={user.avatar_url} alt={user.name} className="h-full w-full object-cover" />
      ) : (
        <span
          className="text-on-accent flex h-full w-full items-center justify-center bg-gradient-to-br from-gold-300 to-gold-500 font-serif font-medium"
          style={{ fontSize: Math.round(size * 0.44) }}
        >
          {avatarChar}
        </span>
      )}
    </span>
  );
}

/** Sidebar account row: avatar, name and VIP mark, linking to the profile. */
export function UserBadge({ collapsed = false }: { collapsed?: boolean }) {
  const { user } = useAuth();
  if (!user) return null;

  return (
    <Link
      to="/profile"
      className={`group flex min-w-0 flex-1 items-center gap-2.5 rounded-lg px-1.5 py-1.5 transition-colors hover:bg-warm-100/5 ${
        collapsed ? "justify-center" : ""
      }`}
      title={collapsed ? `${user.name} · 个人中心` : "个人中心"}
    >
      <UserAvatar size={30} />
      {!collapsed && (
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-1.5">
            <span className="truncate text-[13px] font-medium text-warm-100">{user.name}</span>
            {user.is_vip && (
              <span className="badge-gold px-1.5">
                <Crown className="mr-0.5 h-2.5 w-2.5" strokeWidth={2.2} />
                VIP
              </span>
            )}
          </span>
          <span className="block truncate text-[11px] text-warm-500">{user.email || user.phone || "个人中心"}</span>
        </span>
      )}
    </Link>
  );
}
