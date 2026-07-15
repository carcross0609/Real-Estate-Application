"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { PanelLeftClose, PanelLeftOpen } from "lucide-react";
import { fetchAlerts } from "@/lib/api";
import { useAppStore } from "@/stores/app-store";
import { LogoMark, Wordmark } from "./logo";
import { NAV_SECTIONS } from "./nav-items";
import { Tip } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

export function Sidebar() {
  const pathname = usePathname();
  const collapsed = useAppStore((s) => s.sidebarCollapsed);
  const toggle = useAppStore((s) => s.toggleSidebar);
  const { data: alerts } = useQuery({ queryKey: ["alerts"], queryFn: fetchAlerts });
  const unread = alerts?.filter((a) => !a.read).length ?? 0;

  return (
    <aside
      className={cn(
        "sticky top-0 z-30 hidden h-svh shrink-0 flex-col border-r border-stroke bg-panel md:flex",
        "transition-[width] duration-250 ease-[var(--ease-swift)]",
        collapsed ? "w-[60px]" : "w-[224px]",
      )}
    >
      <div className={cn("flex h-14 items-center gap-2.5 border-b border-stroke", collapsed ? "justify-center px-0" : "px-4")}>
        <Link href="/dashboard" className="flex items-center gap-2.5 rounded-md focus-visible:outline-2" aria-label="DealLens home">
          <LogoMark />
          {!collapsed && <Wordmark />}
        </Link>
      </div>

      <nav className="flex-1 overflow-y-auto px-2.5 py-3" aria-label="Primary">
        {NAV_SECTIONS.map((section) => (
          <div key={section.label} className="mb-4">
            {!collapsed && <div className="eyebrow mb-1.5 px-2">{section.label}</div>}
            <ul className="space-y-0.5">
              {section.items.map((item) => {
                const active = pathname === item.href || pathname.startsWith(item.href + "/");
                const link = (
                  <Link
                    href={item.href}
                    aria-current={active ? "page" : undefined}
                    className={cn(
                      "group relative flex items-center gap-2.5 rounded-md px-2 py-1.5 text-[13px] font-medium",
                      "transition-colors duration-150",
                      collapsed && "justify-center px-0 py-2",
                      active
                        ? "bg-accent-soft text-ink"
                        : "text-ink-2 hover:bg-raise hover:text-ink",
                    )}
                  >
                    {active && (
                      <span className="absolute left-0 top-1/2 h-4 w-0.5 -translate-y-1/2 rounded-full bg-accent" aria-hidden />
                    )}
                    <item.icon
                      className={cn("size-4 shrink-0 transition-colors", active ? "text-accent-ink" : "text-ink-3 group-hover:text-ink-2")}
                      aria-hidden
                    />
                    {!collapsed && <span className="truncate">{item.label}</span>}
                    {item.badge === "alerts" && unread > 0 ? (
                      collapsed ? (
                        <span className="absolute right-1.5 top-1.5 size-1.5 rounded-full bg-accent" aria-label={`${unread} unread`} />
                      ) : (
                        <span className="figure ml-auto rounded-full bg-accent px-1.5 py-px text-[10px] font-semibold text-white">
                          {unread}
                        </span>
                      )
                    ) : null}
                  </Link>
                );
                return (
                  <li key={item.href}>
                    {collapsed ? (
                      <Tip label={item.label} side="right">
                        {link}
                      </Tip>
                    ) : (
                      link
                    )}
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </nav>

      <div className={cn("border-t border-stroke p-2.5", collapsed && "flex justify-center")}>
        <button
          onClick={toggle}
          className={cn(
            "flex items-center gap-2 rounded-md px-2 py-1.5 text-xs text-ink-3 transition-colors hover:bg-raise hover:text-ink-2",
            collapsed && "justify-center px-0 py-2",
          )}
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        >
          {collapsed ? <PanelLeftOpen className="size-4" /> : <PanelLeftClose className="size-4" />}
          {!collapsed && "Collapse"}
        </button>
      </div>
    </aside>
  );
}
