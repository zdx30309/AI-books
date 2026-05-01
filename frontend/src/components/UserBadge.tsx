import { ChevronDown } from "lucide-react";

export function UserBadge() {
  return (
    <div className="absolute right-10 top-8 flex items-center gap-3">
      <div className="relative h-10 w-10 overflow-hidden rounded-full ring-2 ring-gold-400/40">
        <div className="flex h-full w-full items-center justify-center bg-gradient-to-br from-gold-300 to-gold-600 font-serif text-base font-medium text-base-900">
          墨
        </div>
      </div>
      <div className="flex items-center gap-2">
        <span className="text-sm tracking-wide text-warm-200">墨语者</span>
        <span className="badge-gold">VIP</span>
        <ChevronDown
          className="h-4 w-4 text-warm-400"
          strokeWidth={1.5}
        />
      </div>
    </div>
  );
}
