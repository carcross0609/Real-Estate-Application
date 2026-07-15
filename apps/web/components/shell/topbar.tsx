"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { useTheme } from "next-themes";
import {
  Bell,
  ChevronsUpDown,
  Menu,
  Moon,
  Search,
  Sun,
  MapPin,
  Crosshair,
  LogOut,
  UserRound,
  CreditCard,
} from "lucide-react";
import { fetchMarkets, fetchAlerts, fetchCurrentUser } from "@/lib/api";
import { useAppStore } from "@/stores/app-store";
import { STRATEGY_LABEL, type Strategy } from "@/lib/domain";
import { ago } from "@/lib/format";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Sheet, SheetContent, SheetTrigger, SheetTitle } from "@/components/ui/sheet";
import { Tip } from "@/components/ui/tooltip";
import { LogoMark, Wordmark } from "./logo";
import { NAV_SECTIONS } from "./nav-items";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";

const STRATEGY_OPTIONS: Strategy[] = ["overall", "flip", "ltr", "brrrr"];

function MobileNav() {
  const pathname = usePathname();
  const [open, setOpen] = React.useState(false);
  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger asChild>
        <Button variant="ghost" size="icon" className="md:hidden" aria-label="Open navigation">
          <Menu />
        </Button>
      </SheetTrigger>
      <SheetContent side="left" className="w-72">
        <SheetTitle className="sr-only">Navigation</SheetTitle>
        <div className="flex h-14 items-center gap-2.5 border-b border-stroke px-4">
          <LogoMark />
          <Wordmark />
        </div>
        <nav className="flex-1 overflow-y-auto px-3 py-4" aria-label="Primary">
          {NAV_SECTIONS.map((section) => (
            <div key={section.label} className="mb-5">
              <div className="eyebrow mb-1.5 px-2">{section.label}</div>
              <ul className="space-y-0.5">
                {section.items.map((item) => {
                  const active = pathname === item.href || pathname.startsWith(item.href + "/");
                  return (
                    <li key={item.href}>
                      <Link
                        href={item.href}
                        onClick={() => setOpen(false)}
                        className={cn(
                          "flex items-center gap-3 rounded-md px-2.5 py-2 text-sm font-medium transition-colors",
                          active ? "bg-accent-soft text-ink" : "text-ink-2 hover:bg-raise hover:text-ink",
                        )}
                      >
                        <item.icon className={cn("size-4", active ? "text-accent-ink" : "text-ink-3")} />
                        {item.label}
                      </Link>
                    </li>
                  );
                })}
              </ul>
            </div>
          ))}
        </nav>
      </SheetContent>
    </Sheet>
  );
}

function ThemeToggle() {
  const { resolvedTheme, setTheme } = useTheme();
  const [mounted, setMounted] = React.useState(false);
  React.useEffect(() => setMounted(true), []);
  return (
    <Tip label={mounted && resolvedTheme === "dark" ? "Switch to light theme" : "Switch to dark theme"}>
      <Button
        variant="ghost"
        size="icon"
        aria-label="Toggle theme"
        onClick={() => setTheme(resolvedTheme === "dark" ? "light" : "dark")}
      >
        {mounted && resolvedTheme === "light" ? <Sun /> : <Moon />}
      </Button>
    </Tip>
  );
}

export function Topbar({ onOpenCommand }: { onOpenCommand: () => void }) {
  const router = useRouter();
  const marketId = useAppStore((s) => s.marketId);
  const strategy = useAppStore((s) => s.strategy);
  const setMarket = useAppStore((s) => s.setMarket);
  const setStrategy = useAppStore((s) => s.setStrategy);

  const { data: markets } = useQuery({ queryKey: ["markets"], queryFn: fetchMarkets });
  const { data: alerts } = useQuery({ queryKey: ["alerts"], queryFn: fetchAlerts });
  const { data: user } = useQuery({ queryKey: ["me"], queryFn: fetchCurrentUser });

  const market = markets?.find((m) => m.market_id === marketId);
  const unread = alerts?.filter((a) => !a.read).length ?? 0;

  return (
    <header className="sticky top-0 z-30 flex h-14 shrink-0 items-center gap-2 border-b border-stroke bg-canvas/85 px-3 backdrop-blur-md md:px-5">
      <MobileNav />
      <Link href="/dashboard" className="flex items-center gap-2 md:hidden" aria-label="DealLens home">
        <LogoMark className="size-5" />
      </Link>

      {/* Market dial */}
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            className={cn(
              "flex h-8 cursor-pointer items-center gap-1.5 rounded-md border border-stroke bg-card px-2.5",
              "text-[13px] font-medium text-ink transition-colors hover:border-stroke-strong",
            )}
            aria-label="Select market"
          >
            <MapPin className="size-3.5 text-accent-ink" aria-hidden />
            <span className="max-w-28 truncate sm:max-w-none">
              {market ? `${market.name}, ${market.state}` : "Market"}
            </span>
            <ChevronsUpDown className="size-3 text-ink-3" aria-hidden />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="w-64">
          <DropdownMenuLabel>Your markets</DropdownMenuLabel>
          {markets?.map((m) => (
            <DropdownMenuItem key={m.market_id} onClick={() => setMarket(m.market_id)}>
              <MapPin />
              <span className="flex-1">
                {m.name}, {m.state}
              </span>
              <span className="figure text-[11px] text-ink-3">{m.active_listings.toLocaleString()} active</span>
            </DropdownMenuItem>
          ))}
          <DropdownMenuSeparator />
          <DropdownMenuItem onClick={() => router.push("/settings?tab=markets")}>
            Manage markets… <span className="figure ml-auto text-[11px] text-ink-3">{markets?.length ?? 0}/5</span>
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      {/* Strategy dial */}
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button
            className={cn(
              "hidden h-8 cursor-pointer items-center gap-1.5 rounded-md border border-stroke bg-card px-2.5 sm:flex",
              "text-[13px] font-medium text-ink transition-colors hover:border-stroke-strong",
            )}
            aria-label="Select strategy"
          >
            <Crosshair className="size-3.5 text-accent-ink" aria-hidden />
            {STRATEGY_LABEL[strategy]}
            <ChevronsUpDown className="size-3 text-ink-3" aria-hidden />
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="w-52">
          <DropdownMenuLabel>Rank deals by</DropdownMenuLabel>
          {STRATEGY_OPTIONS.map((s) => (
            <DropdownMenuItem key={s} onClick={() => setStrategy(s)}>
              {STRATEGY_LABEL[s]}
              {s === strategy && <span className="ml-auto size-1.5 rounded-full bg-accent" />}
            </DropdownMenuItem>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>

      {/* Data freshness — trust surface (§21 #9/#10) */}
      {market && (
        <Tip label={`Feed healthy · listings as of ${ago(market.as_of)}`}>
          <div className="hidden items-center gap-1.5 pl-1 lg:flex" role="status">
            <span className="relative flex size-1.5">
              <span className="absolute size-full animate-pulse-dot rounded-full bg-pos" />
            </span>
            <span className="figure text-[11px] text-ink-3">live · {ago(market.as_of)}</span>
          </div>
        </Tip>
      )}

      <div className="flex-1" />

      {/* Search / command */}
      <button
        onClick={onOpenCommand}
        className={cn(
          "hidden h-8 w-64 cursor-pointer items-center gap-2 rounded-md border border-stroke bg-panel px-2.5 text-[13px] text-ink-3",
          "transition-colors hover:border-stroke-strong hover:text-ink-2 md:flex",
        )}
        aria-label="Search properties and pages"
      >
        <Search className="size-3.5" aria-hidden />
        <span className="flex-1 text-left">Search address, page…</span>
        <kbd className="figure rounded border border-stroke bg-card px-1 py-px text-[10px] text-ink-3">⌘K</kbd>
      </button>
      <Button variant="ghost" size="icon" className="md:hidden" onClick={onOpenCommand} aria-label="Search">
        <Search />
      </Button>

      <Tip label={unread ? `${unread} unread alerts` : "Alerts"}>
        <Link
          href="/alerts"
          className="relative flex size-8 items-center justify-center rounded-md text-ink-2 transition-colors hover:bg-raise hover:text-ink"
          aria-label={`Alerts${unread ? ` — ${unread} unread` : ""}`}
        >
          <Bell className="size-4" />
          {unread > 0 && (
            <span className="figure absolute -right-0.5 -top-0.5 flex size-4 items-center justify-center rounded-full bg-accent text-[9.5px] font-bold text-white ring-2 ring-canvas">
              {unread}
            </span>
          )}
        </Link>
      </Tip>

      <ThemeToggle />

      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <button className="ml-0.5 cursor-pointer rounded-full transition-transform hover:scale-105 focus-visible:outline-2" aria-label="Account menu">
            <Avatar>
              <AvatarFallback>
                {user?.name
                  .split(" ")
                  .map((p) => p[0])
                  .join("") ?? "··"}
              </AvatarFallback>
            </Avatar>
          </button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-56">
          <div className="px-2 py-1.5">
            <div className="text-[13px] font-medium text-ink">{user?.name}</div>
            <div className="truncate text-[11.5px] text-ink-3">{user?.email}</div>
          </div>
          <DropdownMenuSeparator />
          <DropdownMenuItem onClick={() => router.push("/settings")}>
            <UserRound /> Profile & preferences
          </DropdownMenuItem>
          <DropdownMenuItem onClick={() => router.push("/settings?tab=billing")}>
            <CreditCard /> Billing
            <span className="ml-auto rounded-sm bg-accent-soft px-1.5 py-px font-mono text-[10px] font-semibold uppercase text-accent-ink">
              {user?.plan}
            </span>
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem destructive>
            <LogOut /> Sign out
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </header>
  );
}
