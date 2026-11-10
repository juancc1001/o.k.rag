// The four read-only endpoints of DESIGN §11. Shapes mirror what the server
// returns; nothing here computes, so the view and the MCP tools cannot disagree.

export type Trust = "unverified" | "machine-confirmed" | "human-reviewed";

export interface GraphNode {
  id: string;
  title: string;
  type: string;
  status: string;
  trust: Trust;
  stale: boolean;
  x: number;
  y: number;
  degree: number;
}

export interface GraphLink {
  source: string;
  target: string;
  kind: "explicit";
}

export interface GraphGhost {
  source: string;
  target: string;
  kind: "semantic";
  similarity: number;
}

export interface GraphStats {
  concepts: number;
  links: number;
  ghosts: number;
  broken_links: number;
}

export interface Graph {
  nodes: GraphNode[];
  links: GraphLink[];
  ghosts: GraphGhost[];
  stats: GraphStats;
}

export interface Concept {
  concept_id: string;
  frontmatter: Record<string, unknown>;
  body: string;
  html: string;
}

export interface Hit {
  concept_id: string;
  title: string;
  description: string;
  score: number;
  confidence: number;
  status: string | null;
  trust: string;
}

export interface SearchResult {
  path: "embeddings" | "index-traversal";
  note: string;
  results: Hit[];
}

export interface LintReport {
  conformant: boolean;
  concepts: number;
  errors: string[];
  warnings: string[];
  okflint_exit: number | null;
  okflint_note: string;
  ghosts: GraphGhost[];
  /** Capped server-side; `ghosts_total` is how many the graph holds. */
  ghosts_total: number;
  ghosts_note: string;
  stale: string[];
  orphans: string[];
}

async function get<T>(path: string): Promise<T> {
  const response = await fetch(path);
  if (!response.ok) {
    const detail = await response.json().catch(() => null);
    throw new Error(detail?.detail ?? `${response.status} ${response.statusText}`);
  }
  return response.json() as Promise<T>;
}

export const fetchGraph = () => get<Graph>("/api/graph");
export const fetchConcept = (id: string) => get<Concept>(`/api/concept/${id}`);
export const fetchLint = () => get<LintReport>("/api/lint");
export const fetchSearch = (q: string, k = 8) =>
  get<SearchResult>(`/api/search?q=${encodeURIComponent(q)}&k=${k}`);
