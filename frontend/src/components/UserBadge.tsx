import { Link } from "react-router-dom";
import { Crown, ChevronDown } from "lucide-react";
import { useAuth } from "@/auth/AuthContext";

export function UserBadge() {
  const { user } = useAuth();

  if (!user) return null;

  const avatarChar = (user.name || "我").trim().charAt(0);

  return (
    <Link
      to="/profile"
      className="absolute right-10 top-8 flex items-center gap-3 transition-opacity hover:opacity-90"
    >
      <div className="relative h-10 w-10 overflow-hidden rounded-full ring-2 ring-gold-400/40">
        {user.avatar_url ? (
          <img
            src={user.avatar_url}
            alt={user.name}
            className="h-full w-full object-cover"
          />
        ) : (
          <div className="flex h-full w-full items-center justify-center bg-gradient-to-br from-gold-300 to-gold-600 font-serif text-base font-medium text-base-900">
            {avatarChar}
          </div>
        )}
      </div>
      <div className="flex items-center gap-2">
        <span className="text-sm tracking-wide text-warm-200">{user.name}</span>
        {user.is_vip && (
          <span className="badge-gold">
            <Crown className="mr-1 inline h-3 w-3" strokeWidth={2} />
            VIP
          </span>
        )}
        <ChevronDown className="h-4 w-4 text-warm-400" strokeWidth={1.5} />
      </div>
    </Link>
  );
}
