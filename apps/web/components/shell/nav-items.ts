import {
  LayoutDashboard,
  Map,
  LineChart,
  Bookmark,
  Bell,
  Briefcase,
  Settings,
  type LucideIcon,
} from "lucide-react";

export interface NavItem {
  href: string;
  label: string;
  icon: LucideIcon;
  badge?: "alerts";
}

export const NAV_SECTIONS: Array<{ label: string; items: NavItem[] }> = [
  {
    label: "Analyze",
    items: [
      { href: "/dashboard", label: "Top Deals", icon: LayoutDashboard },
      { href: "/explorer", label: "Market Explorer", icon: Map },
      { href: "/markets", label: "Market Analysis", icon: LineChart },
    ],
  },
  {
    label: "Track",
    items: [
      { href: "/watchlist", label: "Watchlist", icon: Bookmark },
      { href: "/alerts", label: "Alerts", icon: Bell, badge: "alerts" },
      { href: "/portfolio", label: "Portfolio", icon: Briefcase },
    ],
  },
  {
    label: "Workspace",
    items: [{ href: "/settings", label: "Settings", icon: Settings }],
  },
];
