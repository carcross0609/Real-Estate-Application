"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { Command } from "cmdk";
import { useQuery } from "@tanstack/react-query";
import { Search, ArrowRight, Building2 } from "lucide-react";
import { searchProperties } from "@/lib/api";
import { NAV_SECTIONS } from "./nav-items";
import { ScoreChip } from "@/components/deal/grade-ring";
import { moneyCompact } from "@/lib/format";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";

export function CommandPalette({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
}) {
  const router = useRouter();
  const { data: cards } = useQuery({
    queryKey: ["all-properties"],
    queryFn: () => searchProperties({ limit: 400 }),
    enabled: open,
  });

  const go = (href: string) => {
    onOpenChange(false);
    router.push(href);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="top-[18%] max-w-xl translate-y-0 overflow-hidden p-0">
        <DialogTitle className="sr-only">Search</DialogTitle>
        <Command label="Search" loop className="[&_[cmdk-group-heading]]:eyebrow">
          <div className="flex items-center gap-2.5 border-b border-stroke px-4">
            <Search className="size-4 shrink-0 text-ink-3" aria-hidden />
            <Command.Input
              placeholder="Search addresses, pages, actions…"
              className="h-12 w-full bg-transparent text-sm text-ink placeholder:text-ink-3 focus:outline-none"
            />
          </div>
          <Command.List className="max-h-80 overflow-y-auto p-2">
            <Command.Empty className="px-3 py-8 text-center text-[13px] text-ink-3">
              No matches. Try a street name or page.
            </Command.Empty>
            <Command.Group heading="Pages" className="[&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1.5">
              {NAV_SECTIONS.flatMap((s) => s.items).map((item) => (
                <Command.Item
                  key={item.href}
                  value={`page ${item.label}`}
                  onSelect={() => go(item.href)}
                  className="flex cursor-pointer items-center gap-2.5 rounded-md px-2 py-2 text-[13px] text-ink-2 data-[selected=true]:bg-raise data-[selected=true]:text-ink"
                >
                  <item.icon className="size-4 text-ink-3" />
                  {item.label}
                  <ArrowRight className="ml-auto size-3.5 text-ink-faint" />
                </Command.Item>
              ))}
            </Command.Group>
            <Command.Group heading="Properties" className="[&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1.5">
              {cards?.slice(0, 60).map((c) => (
                <Command.Item
                  key={c.property_id}
                  value={`${c.line1} ${c.city} ${c.zip}`}
                  onSelect={() => go(`/property/${c.property_id}`)}
                  className="flex cursor-pointer items-center gap-2.5 rounded-md px-2 py-2 text-[13px] text-ink-2 data-[selected=true]:bg-raise data-[selected=true]:text-ink"
                >
                  <Building2 className="size-4 shrink-0 text-ink-3" />
                  <span className="min-w-0 flex-1 truncate">
                    {c.line1}
                    <span className="text-ink-3"> · {c.city}</span>
                  </span>
                  <span className="figure text-xs text-ink-3">{moneyCompact(c.list_price)}</span>
                  <ScoreChip score={c.score} grade={c.grade} />
                </Command.Item>
              ))}
            </Command.Group>
          </Command.List>
        </Command>
      </DialogContent>
    </Dialog>
  );
}
