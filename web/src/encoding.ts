// The visual encoding of DESIGN §8, in one place so the legend and the renderer
// cannot drift apart.
//
// One deviation from §8, deliberate: a ghost edge is drawn thin and pale rather
// than dashed. sigma v3 has no dashed edge program, and a custom one is more
// machinery than the distinction needs — colour and weight already separate the
// two layers at a glance.

import type { GraphNode, Trust } from "./api";

const PALETTE = [
  "#4c72b0",
  "#dd8452",
  "#55a868",
  "#c44e52",
  "#8172b3",
  "#937860",
  "#da8bc3",
  "#8c8c8c",
  "#ccb974",
  "#64b5cd",
];

export const LINK_COLOR = "#9aa4b2";
export const GHOST_COLOR = "#d8c3e8";
export const FADED = "#dfe3e8";

/** Colour by `type`, assigned over the sorted vocabulary so it is stable. */
export function typeColors(nodes: GraphNode[]): Map<string, string> {
  const types = [...new Set(nodes.map((n) => n.type))].sort();
  return new Map(types.map((t, i) => [t, PALETTE[i % PALETTE.length]]));
}

/** Size by link degree. sqrt so a hub does not swamp the canvas. */
export function nodeSize(degree: number): number {
  return 4 + Math.sqrt(degree) * 2.5;
}

/** Trust tier as a border width: none / thin / solid (§8). */
export function borderWidth(trust: Trust): number {
  if (trust === "human-reviewed") return 0.28;
  if (trust === "machine-confirmed") return 0.14;
  return 0;
}

/** Deprecated or past `stale_after` reads as receded, not as absent. */
export function isDimmed(node: GraphNode): boolean {
  return node.status === "deprecated" || node.stale;
}

export function dim(color: string, amount = 0.65): string {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(color.slice(i, i + 2), 16));
  const mix = (c: number) => Math.round(c + (255 - c) * amount);
  return `#${[mix(r), mix(g), mix(b)].map((c) => c.toString(16).padStart(2, "0")).join("")}`;
}
