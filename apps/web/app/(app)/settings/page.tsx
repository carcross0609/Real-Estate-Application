"use client";

/** S30–S33 — Settings: profile & assumption defaults, notifications, billing & usage,
 * team & roles & API keys, markets, appearance. One surface, tabbed. */

import * as React from "react";
import { Suspense } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { useTheme } from "next-themes";
import {
  UserRound,
  Bell,
  CreditCard,
  Users,
  MapPin,
  Palette,
  Plus,
  Trash2,
  KeyRound,
  Moon,
  Sun,
  Crown,
  ShieldCheck,
  Eye,
  Wrench,
} from "lucide-react";
import { fetchCurrentUser, fetchTeam, fetchApiKeys, fetchMarkets } from "@/lib/api";
import { STRATEGY_LABEL } from "@/lib/domain";
import { moneyCompact, num, ago, dateShort } from "@/lib/format";
import { SectionHeader } from "@/components/deal/stat";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input, UnitInput } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { toast } from "sonner";
import { cn } from "@/lib/utils";

function Row({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-2 py-3">
      <div className="min-w-0">
        <div className="text-[13px] font-medium text-ink">{label}</div>
        {hint && <div className="mt-0.5 max-w-md text-xs leading-relaxed text-ink-3">{hint}</div>}
      </div>
      <div className="flex items-center gap-2">{children}</div>
    </div>
  );
}

const ROLE_META = {
  owner: { icon: Crown, label: "Owner" },
  admin: { icon: ShieldCheck, label: "Admin" },
  analyst: { icon: Wrench, label: "Analyst" },
  viewer: { icon: Eye, label: "Viewer" },
} as const;

function SettingsInner() {
  const search = useSearchParams();
  const router = useRouter();
  const tab = search.get("tab") ?? "profile";
  const { resolvedTheme, setTheme } = useTheme();

  const { data: user } = useQuery({ queryKey: ["me"], queryFn: fetchCurrentUser });
  const { data: team } = useQuery({ queryKey: ["team"], queryFn: fetchTeam });
  const { data: keys } = useQuery({ queryKey: ["api-keys"], queryFn: fetchApiKeys });
  const { data: markets } = useQuery({ queryKey: ["markets"], queryFn: fetchMarkets });

  const saved = () => toast("Saved", { description: "Preference updated for your workspace." });

  return (
    <div className="mx-auto max-w-[980px] animate-fade-up px-4 py-5 md:px-6">
      <SectionHeader eyebrow={user?.org ?? "…"} title="Settings" className="mb-4" />

      <Tabs value={tab} onValueChange={(v) => router.replace(`/settings?tab=${v}`, { scroll: false })}>
        <TabsList className="mb-2 h-auto flex-wrap">
          <TabsTrigger value="profile"><UserRound className="size-3.5" /> Profile</TabsTrigger>
          <TabsTrigger value="notifications"><Bell className="size-3.5" /> Notifications</TabsTrigger>
          <TabsTrigger value="billing"><CreditCard className="size-3.5" /> Billing</TabsTrigger>
          <TabsTrigger value="team"><Users className="size-3.5" /> Team</TabsTrigger>
          <TabsTrigger value="markets"><MapPin className="size-3.5" /> Markets</TabsTrigger>
          <TabsTrigger value="appearance"><Palette className="size-3.5" /> Appearance</TabsTrigger>
        </TabsList>

        {/* ------------- Profile ------------- */}
        <TabsContent value="profile" className="space-y-4">
          <Card>
            <CardHeader><CardTitle>Identity</CardTitle></CardHeader>
            <CardContent className="pt-3">
              <div className="mb-4 flex items-center gap-3">
                <Avatar className="size-12">
                  <AvatarFallback className="text-base">
                    {user?.name.split(" ").map((p) => p[0]).join("") ?? "··"}
                  </AvatarFallback>
                </Avatar>
                <div>
                  <div className="text-sm font-semibold text-ink">{user?.name}</div>
                  <div className="text-xs text-ink-3">{user?.email}</div>
                </div>
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                <div>
                  <label className="eyebrow mb-1 block" htmlFor="name">Full name</label>
                  <Input id="name" defaultValue={user?.name} onBlur={saved} />
                </div>
                <div>
                  <label className="eyebrow mb-1 block" htmlFor="org">Workspace</label>
                  <Input id="org" defaultValue={user?.org} onBlur={saved} />
                </div>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Underwriting defaults</CardTitle>
              <span className="figure text-[10.5px] text-ink-3">seed every analyzer session</span>
            </CardHeader>
            <CardContent className="divide-y divide-hairline pt-1">
              <Row label="Default strategy" hint="Ranks the dashboard and explorer when 'Best Strategy' is off">
                <Select defaultValue="overall" onValueChange={saved}>
                  <SelectTrigger className="w-44"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {(["overall", "flip", "ltr", "brrrr"] as const).map((s) => (
                      <SelectItem key={s} value={s}>{STRATEGY_LABEL[s]}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </Row>
              <Row label="Financing rate" hint="Applied when a live lender quote isn't attached">
                <UnitInput suffix="%" defaultValue="6.90" className="w-28" onBlur={saved} />
              </Row>
              <Row label="Management fee">
                <UnitInput suffix="%" defaultValue="9" className="w-28" onBlur={saved} />
              </Row>
              <Row label="Vacancy reserve">
                <UnitInput suffix="%" defaultValue="6" className="w-28" onBlur={saved} />
              </Row>
              <Row label="Flip margin floor" hint="Deals under this margin are down-scored for you">
                <UnitInput suffix="%" defaultValue="12" className="w-28" onBlur={saved} />
              </Row>
            </CardContent>
          </Card>
        </TabsContent>

        {/* ------------- Notifications ------------- */}
        <TabsContent value="notifications">
          <Card>
            <CardHeader><CardTitle>Delivery channels</CardTitle></CardHeader>
            <CardContent className="divide-y divide-hairline pt-1">
              <Row label="Instant push" hint="New matches and price cuts on watched properties, within 15 minutes of ingest">
                <Switch defaultChecked onCheckedChange={saved} aria-label="Instant push" />
              </Row>
              <Row label="Email alerts" hint="Same triggers as push, for when you're off the terminal">
                <Switch defaultChecked onCheckedChange={saved} aria-label="Email alerts" />
              </Row>
              <Row label="Daily digest" hint="One 7 AM summary per market: new Top-25 entrants, movers, expired watches">
                <Switch defaultChecked onCheckedChange={saved} aria-label="Daily digest" />
              </Row>
              <Row label="Rate cap" hint="Alerts beyond this hourly cap fold into a digest — protects deliverability and your attention">
                <Select defaultValue="5" onValueChange={saved}>
                  <SelectTrigger className="w-28"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {["3", "5", "10", "20"].map((n) => (
                      <SelectItem key={n} value={n}>{n}/hour</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </Row>
              <Row label="Feed-health notices" hint="Tell me when a market feed degrades or recovers">
                <Switch defaultChecked onCheckedChange={saved} aria-label="Feed health notices" />
              </Row>
            </CardContent>
          </Card>
        </TabsContent>

        {/* ------------- Billing ------------- */}
        <TabsContent value="billing" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>Plan</CardTitle>
              <Badge variant="accent" className="uppercase">{user?.plan ?? "…"}</Badge>
            </CardHeader>
            <CardContent className="pt-3">
              <div className="flex flex-wrap items-end justify-between gap-3">
                <div>
                  <div className="figure text-2xl font-semibold text-ink">$149<span className="text-sm font-normal text-ink-3">/mo</span></div>
                  <div className="mt-0.5 text-xs text-ink-3">Renews {user ? dateShort(user.renews_at) : "…"} · billed monthly</div>
                </div>
                <div className="flex gap-2">
                  <Button variant="secondary" onClick={() => toast("Opening Stripe portal", { description: "Invoices, payment method, and plan changes live there." })}>
                    Manage in Stripe
                  </Button>
                  <Button variant="primary" onClick={() => toast("Upgrade flow", { description: "Team plan adds 3 seats, 2 markets, and shared assumption locks." })}>
                    Upgrade to Team
                  </Button>
                </div>
              </div>
              <Separator className="my-4" />
              <div className="grid gap-4 sm:grid-cols-3">
                {user && (
                  [
                    { label: "Markets", used: user.markets_used, total: user.markets_total },
                    { label: "Seats", used: user.seats_used, total: user.seats_total },
                    { label: "AI reports this cycle", used: user.reports_used, total: user.reports_total },
                  ] as const
                ).map((u) => (
                  <div key={u.label}>
                    <div className="mb-1.5 flex items-baseline justify-between">
                      <span className="eyebrow">{u.label}</span>
                      <span className="figure text-xs font-medium text-ink">{u.used}/{u.total}</span>
                    </div>
                    <Progress value={(u.used / u.total) * 100} indicatorColor={u.used / u.total > 0.85 ? "var(--grade-c)" : undefined} />
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        {/* ------------- Team ------------- */}
        <TabsContent value="team" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle>Members</CardTitle>
              <Button size="sm" variant="primary" onClick={() => toast("Invite sent", { description: "They'll get an email with a join link — seats update instantly." })}>
                <Plus className="size-3.5" /> Invite
              </Button>
            </CardHeader>
            <CardContent className="divide-y divide-hairline pt-1">
              {team?.map((m) => {
                const role = ROLE_META[m.role];
                return (
                  <div key={m.user_id} className="flex flex-wrap items-center gap-3 py-2.5">
                    <Avatar>
                      <AvatarFallback>{m.name.split(" ").map((p) => p[0]).join("")}</AvatarFallback>
                    </Avatar>
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-[13px] font-medium text-ink">{m.name}</span>
                        {m.status === "invited" && <Badge variant="warn">Invite pending</Badge>}
                      </div>
                      <div className="truncate text-xs text-ink-3">{m.email}</div>
                    </div>
                    <span className="figure hidden text-[11px] text-ink-3 sm:block">
                      {m.last_active ? `active ${ago(m.last_active)}` : "never signed in"}
                    </span>
                    <Select defaultValue={m.role} onValueChange={saved} disabled={m.role === "owner"}>
                      <SelectTrigger className="w-32" aria-label={`Role for ${m.name}`}>
                        <span className="flex items-center gap-1.5">
                          <role.icon className="size-3.5 text-ink-3" />
                          <SelectValue />
                        </span>
                      </SelectTrigger>
                      <SelectContent>
                        {Object.entries(ROLE_META).map(([k, v]) => (
                          <SelectItem key={k} value={k} disabled={k === "owner"}>{v.label}</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <Button
                      size="icon-sm"
                      variant="ghost"
                      disabled={m.role === "owner"}
                      aria-label={`Remove ${m.name}`}
                      onClick={() => toast("Member removed", { description: `${m.name} loses access immediately; their notes stay with the workspace.` })}
                    >
                      <Trash2 className="text-ink-3" />
                    </Button>
                  </div>
                );
              }) ?? <Skeleton className="my-3 h-40" />}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>API keys</CardTitle>
              <Button size="sm" variant="secondary" onClick={() => toast("Key created", { description: "Copy it now — it won't be shown again." })}>
                <KeyRound className="size-3.5" /> New key
              </Button>
            </CardHeader>
            <CardContent className="divide-y divide-hairline pt-1">
              {keys?.map((k) => (
                <div key={k.key_id} className="flex flex-wrap items-center gap-3 py-2.5">
                  <div className="flex size-8 items-center justify-center rounded-md bg-raise">
                    <KeyRound className="size-3.5 text-ink-3" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="text-[13px] font-medium text-ink">{k.name}</div>
                    <div className="figure text-[11px] text-ink-3">{k.prefix}•••• · created {dateShort(k.created_at)}</div>
                  </div>
                  <span className="figure text-[11px] text-ink-3">
                    {k.last_used ? `used ${ago(k.last_used)}` : "never used"}
                  </span>
                  <Button size="icon-sm" variant="ghost" aria-label={`Revoke ${k.name}`} onClick={() => toast("Key revoked", { description: "Requests with this key now return 401." })}>
                    <Trash2 className="text-ink-3" />
                  </Button>
                </div>
              )) ?? <Skeleton className="my-3 h-20" />}
            </CardContent>
          </Card>
        </TabsContent>

        {/* ------------- Markets ------------- */}
        <TabsContent value="markets">
          <Card>
            <CardHeader>
              <CardTitle>Your markets</CardTitle>
              <span className="figure text-[11px] text-ink-3">{user ? `${user.markets_used} of ${user.markets_total} slots` : ""}</span>
            </CardHeader>
            <CardContent className="divide-y divide-hairline pt-1">
              {markets?.map((m) => (
                <div key={m.market_id} className="flex flex-wrap items-center gap-3 py-3">
                  <div className="flex size-8 items-center justify-center rounded-md bg-accent-soft">
                    <MapPin className="size-3.5 text-accent-ink" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="text-[13px] font-medium text-ink">{m.name}, {m.state}</div>
                    <div className="figure text-[11px] text-ink-3">
                      {num(m.active_listings)} active · median {moneyCompact(m.median_price)} · feed healthy
                    </div>
                  </div>
                  <span className="flex items-center gap-1.5">
                    <span className="size-1.5 animate-pulse-dot rounded-full bg-pos" aria-hidden />
                    <span className="figure text-[11px] text-ink-3">as of {ago(m.as_of)}</span>
                  </span>
                  <Button size="sm" variant="ghost" onClick={() => toast("Market paused", { description: "Analysis stops, data is retained; resume any time without re-onboarding." })}>
                    Pause
                  </Button>
                </div>
              )) ?? <Skeleton className="my-3 h-32" />}
              <div className="pt-3">
                <Button variant="secondary" onClick={() => toast("Add a market", { description: "Draw a boundary or pick a metro — pre-analyzed metros activate instantly." })}>
                  <Plus className="size-4" /> Add market
                </Button>
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        {/* ------------- Appearance ------------- */}
        <TabsContent value="appearance">
          <Card>
            <CardHeader><CardTitle>Theme</CardTitle></CardHeader>
            <CardContent className="pt-3">
              <div className="grid max-w-md grid-cols-2 gap-3">
                {(
                  [
                    { key: "dark", label: "Dark", icon: Moon, desc: "Terminal default — built for long sessions" },
                    { key: "light", label: "Light", icon: Sun, desc: "Full light theme, same information density" },
                  ] as const
                ).map((t) => (
                  <button
                    key={t.key}
                    onClick={() => setTheme(t.key)}
                    aria-pressed={resolvedTheme === t.key}
                    className={cn(
                      "cursor-pointer rounded-lg border p-3.5 text-left transition-all duration-150",
                      resolvedTheme === t.key
                        ? "border-accent bg-accent-soft/60 shadow-[0_0_0_1px_var(--accent)]"
                        : "border-stroke bg-panel hover:border-stroke-strong",
                    )}
                  >
                    <t.icon className={cn("mb-2 size-4", resolvedTheme === t.key ? "text-accent-ink" : "text-ink-3")} />
                    <div className="text-[13px] font-semibold text-ink">{t.label}</div>
                    <div className="mt-0.5 text-[11.5px] leading-snug text-ink-3">{t.desc}</div>
                  </button>
                ))}
              </div>
              <p className="mt-4 max-w-md text-xs leading-relaxed text-ink-3">
                Grade colors and score ramps are identical in both themes — a B+ reads the same at midnight and
                in the morning sun.
              </p>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
}

export default function SettingsPage() {
  return (
    <Suspense fallback={<div className="p-6"><Skeleton className="h-96 max-w-[980px]" /></div>}>
      <SettingsInner />
    </Suspense>
  );
}
