// The renderer. Positions are precomputed by UMAP and arrive in graph.json, so
// no layout runs in the browser — sigma is a renderer here, not a layout engine
// (§8). Filtering is done in reducers rather than by rebuilding the graph, so
// dragging the τ slider costs a repaint and not a reload.

import { createNodeBorderProgram } from "@sigma/node-border";
import Graphology from "graphology";
import { useEffect, useRef } from "react";
import Sigma from "sigma";

import type { Graph as GraphData } from "./api";
import {
  FADED,
  GHOST_COLOR,
  LINK_COLOR,
  borderWidth,
  dim,
  isDimmed,
  nodeSize,
  typeColors,
} from "./encoding";

const BORDER_COLOR = "#2f3437";
const SELECTED_COLOR = "#111418";

interface Props {
  data: GraphData;
  tau: number;
  showGhosts: boolean;
  selected: string | null;
  matches: Set<string> | null;
  onSelect: (id: string | null) => void;
}

export function Graph({ data, tau, showGhosts, selected, matches, onSelect }: Props) {
  const container = useRef<HTMLDivElement>(null);
  const sigma = useRef<Sigma | null>(null);
  // the reducers read this rather than their closure, so they never see a stale
  // τ and the graph is not rebuilt to change a filter
  const state = useRef({ tau, showGhosts, selected, matches, onSelect });
  state.current = { tau, showGhosts, selected, matches, onSelect };

  useEffect(() => {
    if (!container.current) return;

    const graph = new Graphology({ multi: true });
    const colors = typeColors(data.nodes);
    for (const node of data.nodes) {
      const base = colors.get(node.type) ?? "#8c8c8c";
      graph.addNode(node.id, {
        x: node.x,
        y: node.y,
        label: node.title,
        size: nodeSize(node.degree),
        color: isDimmed(node) ? dim(base) : base,
        borderColor: BORDER_COLOR,
        borderSize: borderWidth(node.trust),
      });
    }
    for (const link of data.links) {
      if (graph.hasNode(link.source) && graph.hasNode(link.target)) {
        graph.addEdge(link.source, link.target, {
          kind: "explicit",
          size: 1.4,
          color: LINK_COLOR,
        });
      }
    }
    for (const ghost of data.ghosts) {
      if (graph.hasNode(ghost.source) && graph.hasNode(ghost.target)) {
        graph.addEdge(ghost.source, ghost.target, {
          kind: "semantic",
          similarity: ghost.similarity,
          size: 0.7,
          color: GHOST_COLOR,
        });
      }
    }

    const renderer = new Sigma(graph, container.current, {
      nodeProgramClasses: {
        border: createNodeBorderProgram({
          borders: [
            { size: { attribute: "borderSize", defaultValue: 0 }, color: { attribute: "borderColor" } },
            { size: { fill: true }, color: { attribute: "color" } },
          ],
        }),
      },
      defaultNodeType: "border",
      labelRenderedSizeThreshold: 7,
      labelDensity: 0.5,
      minCameraRatio: 0.05,
      maxCameraRatio: 12,
    });

    renderer.setSetting("edgeReducer", (edge, attrs) => {
      const { tau: t, showGhosts: ghosts, selected: current } = state.current;
      if (attrs.kind === "semantic" && (!ghosts || (attrs.similarity as number) < t)) {
        return { ...attrs, hidden: true };
      }
      if (current) {
        const [source, target] = graph.extremities(edge);
        if (source !== current && target !== current) return { ...attrs, color: FADED };
      }
      return attrs;
    });

    renderer.setSetting("nodeReducer", (node, attrs) => {
      const { selected: current, matches: hits } = state.current;
      if (hits && !hits.has(node)) return { ...attrs, color: FADED, borderSize: 0, label: "" };
      if (current === node) {
        return { ...attrs, borderColor: SELECTED_COLOR, borderSize: 0.34, zIndex: 1 };
      }
      return attrs;
    });

    renderer.on("clickNode", ({ node }) => state.current.onSelect(node));
    renderer.on("clickStage", () => state.current.onSelect(null));

    sigma.current = renderer;
    return () => {
      renderer.kill();
      sigma.current = null;
    };
  }, [data]);

  useEffect(() => {
    sigma.current?.refresh({ skipIndexation: true });
  }, [tau, showGhosts, selected, matches]);

  return <div className="graph" ref={container} />;
}
