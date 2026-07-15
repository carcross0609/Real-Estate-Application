import { cn } from "@/lib/utils";

/** DealLens mark — an aperture/lens diamond. */
export function LogoMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" className={cn("size-6", className)} aria-hidden>
      <rect x="3.2" y="3.2" width="17.6" height="17.6" rx="5" fill="var(--accent)" opacity="0.16" />
      <rect x="3.2" y="3.2" width="17.6" height="17.6" rx="5" stroke="var(--accent)" strokeWidth="1.4" />
      <path d="M12 6.8 17.2 12 12 17.2 6.8 12Z" stroke="var(--accent)" strokeWidth="1.4" strokeLinejoin="round" />
      <circle cx="12" cy="12" r="1.7" fill="var(--accent)" />
    </svg>
  );
}

export function Wordmark({ className }: { className?: string }) {
  return (
    <span className={cn("select-none text-[15px] font-semibold tracking-tight text-ink", className)}>
      Deal<span className="text-accent-ink">Lens</span>
    </span>
  );
}
