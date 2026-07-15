"use client";

/** Global UI context: selected market × strategy (the terminal's two dials), watchlist,
 * sidebar state. Analyzer state lives in its own store (stores/analyzer-store.ts). */

import { create } from "zustand";
import type { Strategy } from "@/lib/domain";
import { WATCHLIST } from "@/lib/mock/account";

interface AppState {
  marketId: string;
  strategy: Strategy;
  sidebarCollapsed: boolean;
  watched: Set<string>;
  setMarket: (id: string) => void;
  setStrategy: (s: Strategy) => void;
  toggleSidebar: () => void;
  toggleWatch: (propertyId: string) => void;
}

export const useAppStore = create<AppState>((set) => ({
  marketId: "mkt-columbus",
  strategy: "overall",
  sidebarCollapsed: false,
  watched: new Set(WATCHLIST.map((w) => w.property_id)),
  setMarket: (id) => set({ marketId: id }),
  setStrategy: (s) => set({ strategy: s }),
  toggleSidebar: () => set((st) => ({ sidebarCollapsed: !st.sidebarCollapsed })),
  toggleWatch: (propertyId) =>
    set((st) => {
      const next = new Set(st.watched);
      if (next.has(propertyId)) next.delete(propertyId);
      else next.add(propertyId);
      return { watched: next };
    }),
}));
