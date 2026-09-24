import { useId } from "react";

/** ScriptBook mark: an open book on an accent tile. Follows the active theme. */
export function BrandMark({ size = 30, className = "" }: { size?: number; className?: string }) {
  const gradientId = useId();
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      aria-hidden="true"
      className={`shrink-0 ${className}`}
    >
      <defs>
        <linearGradient id={gradientId} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" style={{ stopColor: "rgb(var(--color-gold-200))" }} />
          <stop offset="1" style={{ stopColor: "rgb(var(--color-gold-500))" }} />
        </linearGradient>
      </defs>
      <rect width="32" height="32" rx="9" fill={`url(#${gradientId})`} />
      <path
        d="M8.5 10.2c2.7-1 5.2-.7 7.5 1.2v11.2c-2.2-1.6-4.7-1.9-7.5-1V10.2Z"
        style={{ fill: "rgb(var(--color-button-text))", opacity: 0.92 }}
      />
      <path
        d="M23.5 10.2c-2.7-1-5.2-.7-7.5 1.2v11.2c2.2-1.6 4.7-1.9 7.5-1V10.2Z"
        style={{ fill: "rgb(var(--color-button-text))", opacity: 0.55 }}
      />
    </svg>
  );
}

export function BrandWordmark({ className = "" }: { className?: string }) {
  return (
    <span className={`font-serif text-[17px] leading-none tracking-tight text-warm-50 ${className}`}>
      Script<span className="text-gold-300">Book</span>
    </span>
  );
}
