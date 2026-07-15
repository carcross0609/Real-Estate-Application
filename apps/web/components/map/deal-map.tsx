"use client";

/**
 * DealMap — MapLibre GL with score-colored pins (§21 #7: map and table are peers).
 * Grade color + hover ring mirror the table exactly; hover/selection sync both ways.
 * Basemap: CARTO raster (keyless) over OSM; falls back to a plain canvas if tiles fail.
 */

import * as React from "react";
import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { useTheme } from "next-themes";
import type { PropertyCard } from "@/lib/types";
import { gradeFamily } from "@/lib/domain";
import { moneyCompact } from "@/lib/format";
import { GradeRing } from "@/components/deal/grade-ring";

const GRADE_HEX: Record<string, Record<string, string>> = {
  dark: { a: "#2fd37f", b: "#9ade4b", c: "#f2c239", d: "#f59e4b", f: "#f16a5e" },
  light: { a: "#0e9f5c", b: "#689812", c: "#b98a04", d: "#d9640e", f: "#d9433a" },
};

function baseStyle(theme: string): maplibregl.StyleSpecification {
  const variant = theme === "light" ? "light_all" : "dark_all";
  return {
    version: 8,
    sources: {
      carto: {
        type: "raster",
        tiles: [
          `https://a.basemaps.cartocdn.com/${variant}/{z}/{x}/{y}@2x.png`,
          `https://b.basemaps.cartocdn.com/${variant}/{z}/{x}/{y}@2x.png`,
          `https://c.basemaps.cartocdn.com/${variant}/{z}/{x}/{y}@2x.png`,
        ],
        tileSize: 256,
        attribution: "© OpenStreetMap contributors © CARTO",
      },
    },
    layers: [
      { id: "bg", type: "background", paint: { "background-color": theme === "light" ? "#e8eaef" : "#0c0f16" } },
      { id: "carto", type: "raster", source: "carto", paint: { "raster-opacity": 0.92 } },
    ],
  };
}

function toGeoJSON(cards: PropertyCard[], theme: string) {
  const palette = GRADE_HEX[theme] ?? GRADE_HEX.dark;
  return {
    type: "FeatureCollection" as const,
    features: cards.map((c) => ({
      type: "Feature" as const,
      id: c.property_id.split("-").pop(), // numeric-ish id for feature-state
      properties: {
        pid: c.property_id,
        color: palette[gradeFamily(c.grade)],
        score: Math.round(c.score),
      },
      geometry: { type: "Point" as const, coordinates: [c.lon, c.lat] },
    })),
  };
}

export interface DealMapProps {
  cards: PropertyCard[];
  center: { lat: number; lon: number };
  hoveredId: string | null;
  onHover: (id: string | null) => void;
  onSelect: (id: string) => void;
  className?: string;
}

export function DealMap({ cards, center, hoveredId, onHover, onSelect, className }: DealMapProps) {
  const containerRef = React.useRef<HTMLDivElement>(null);
  const mapRef = React.useRef<maplibregl.Map | null>(null);
  const [ready, setReady] = React.useState(false);
  const { resolvedTheme } = useTheme();
  const theme = resolvedTheme === "light" ? "light" : "dark";
  const [hoverCard, setHoverCard] = React.useState<{ card: PropertyCard; x: number; y: number } | null>(null);

  const cardsRef = React.useRef(cards);
  cardsRef.current = cards;
  const onHoverRef = React.useRef(onHover);
  onHoverRef.current = onHover;
  const onSelectRef = React.useRef(onSelect);
  onSelectRef.current = onSelect;

  const addDataLayers = React.useCallback((map: maplibregl.Map, theme: string) => {
    if (map.getSource("deals")) return;
    map.addSource("deals", { type: "geojson", data: toGeoJSON(cardsRef.current, theme) });
    map.addLayer({
      id: "deal-halo",
      type: "circle",
      source: "deals",
      paint: {
        "circle-radius": ["case", ["boolean", ["feature-state", "hover"], false], 14, 0],
        "circle-color": ["get", "color"],
        "circle-opacity": 0.25,
      },
    });
    map.addLayer({
      id: "deal-pins",
      type: "circle",
      source: "deals",
      paint: {
        "circle-radius": ["interpolate", ["linear"], ["zoom"], 8, 3.5, 11, 6, 14, 9],
        "circle-color": ["get", "color"],
        "circle-stroke-width": ["case", ["boolean", ["feature-state", "hover"], false], 2, 1],
        "circle-stroke-color": theme === "light" ? "#ffffff" : "#0a0d14",
      },
    });
  }, []);

  /* Init once */
  React.useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    const map = new maplibregl.Map({
      container: containerRef.current,
      style: baseStyle(theme),
      center: [center.lon, center.lat],
      zoom: 9.6,
      attributionControl: { compact: true },
    });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
    mapRef.current = map;

    let hoveredFeature: string | number | undefined;
    map.on("load", () => {
      addDataLayers(map, theme);
      setReady(true);
    });
    map.on("mousemove", "deal-pins", (e) => {
      const f = e.features?.[0];
      if (!f) return;
      map.getCanvas().style.cursor = "pointer";
      if (hoveredFeature !== undefined) map.setFeatureState({ source: "deals", id: hoveredFeature }, { hover: false });
      hoveredFeature = f.id;
      map.setFeatureState({ source: "deals", id: f.id }, { hover: true });
      const pid = f.properties?.pid as string;
      onHoverRef.current(pid);
      const card = cardsRef.current.find((c) => c.property_id === pid);
      if (card) {
        const pt = map.project([card.lon, card.lat]);
        setHoverCard({ card, x: pt.x, y: pt.y });
      }
    });
    map.on("mouseleave", "deal-pins", () => {
      map.getCanvas().style.cursor = "";
      if (hoveredFeature !== undefined) map.setFeatureState({ source: "deals", id: hoveredFeature }, { hover: false });
      hoveredFeature = undefined;
      onHoverRef.current(null);
      setHoverCard(null);
    });
    map.on("click", "deal-pins", (e) => {
      const pid = e.features?.[0]?.properties?.pid as string | undefined;
      if (pid) onSelectRef.current(pid);
    });

    return () => {
      map.remove();
      mapRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  /* Data updates */
  React.useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    const src = map.getSource("deals") as maplibregl.GeoJSONSource | undefined;
    src?.setData(toGeoJSON(cards, theme));
  }, [cards, theme, ready]);

  /* Theme swap */
  React.useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    map.setStyle(baseStyle(theme));
    map.once("styledata", () => addDataLayers(map, theme));
  }, [theme, ready, addDataLayers]);

  /* Recenter on market change */
  React.useEffect(() => {
    mapRef.current?.flyTo({ center: [center.lon, center.lat], zoom: 9.6, duration: 900 });
  }, [center.lat, center.lon]);

  /* Table-driven hover → pin highlight */
  const lastTableHover = React.useRef<string | null>(null);
  React.useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready || !map.getSource("deals")) return;
    const fid = (id: string | null) => id?.split("-").pop();
    if (lastTableHover.current) {
      map.setFeatureState({ source: "deals", id: fid(lastTableHover.current)! }, { hover: false });
    }
    if (hoveredId) {
      map.setFeatureState({ source: "deals", id: fid(hoveredId)! }, { hover: true });
    }
    lastTableHover.current = hoveredId;
  }, [hoveredId, ready]);

  return (
    <div className={className}>
      <div ref={containerRef} className="size-full overflow-hidden rounded-lg border border-stroke" />
      {hoverCard && (
        <div
          className="pointer-events-none absolute z-10 w-56 -translate-x-1/2 rounded-lg border border-stroke-strong bg-overlay p-2.5 shadow-[var(--shadow-pop)]"
          style={{ left: hoverCard.x, top: Math.max(8, hoverCard.y - 96) }}
          role="status"
        >
          <div className="flex items-center gap-2.5">
            <GradeRing score={hoverCard.card.score} grade={hoverCard.card.grade} size="sm" animate={false} />
            <div className="min-w-0">
              <div className="truncate text-xs font-semibold text-ink">{hoverCard.card.line1}</div>
              <div className="figure text-[11px] text-ink-3">
                {moneyCompact(hoverCard.card.list_price)} · {hoverCard.card.beds}bd · {hoverCard.card.dom}d
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
