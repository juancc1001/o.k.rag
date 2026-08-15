import { useEffect, useMemo, useState } from "react";

import { fetchGraph, fetchSearch, type Graph as GraphData, type SearchResult } from "./api";
import { Controls, ghostFloor } from "./Controls";
import { Graph } from "./Graph";
import { Panel } from "./Panel";

export function App() {
  const [data, setData] = useState<GraphData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [showGhosts, setShowGhosts] = useState(false); // §8: off by default
  const [tau, setTau] = useState<number | null>(null);
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState<SearchResult | null>(null);
  const [searching, setSearching] = useState(false);

  useEffect(() => {
    fetchGraph()
      .then(setData)
      .catch((exc: Error) => setError(exc.message));
  }, []);

  const floor = useMemo(() => (data ? ghostFloor(data) : 0.8), [data]);
  const threshold = tau ?? floor;

  useEffect(() => {
    const term = query.trim();
    if (!term) {
      setSearch(null);
      return;
    }
    setSearching(true);
    const timer = setTimeout(() => {
      fetchSearch(term)
        .then(setSearch)
        .catch(() => setSearch(null))
        .finally(() => setSearching(false));
    }, 250);
    return () => clearTimeout(timer);
  }, [query]);

  const matches = useMemo(
    () => (search ? new Set(search.results.map((hit) => hit.concept_id)) : null),
    [search],
  );

  const visibleGhosts = useMemo(
    () => (data ? data.ghosts.filter((ghost) => ghost.similarity >= threshold).length : 0),
    [data, threshold],
  );

  if (error) {
    return (
      <div className="empty">
        <h1>okragged</h1>
        <p className="error">{error}</p>
        <p className="note">
          The graph is written by <code>okragged index</code>, which reads the bundle and writes
          into <code>.okragged/</code> only.
        </p>
      </div>
    );
  }

  if (!data) return <div className="empty">loading the graph…</div>;

  return (
    <div className="layout">
      <Controls
        data={data}
        tau={threshold}
        floor={floor}
        visibleGhosts={visibleGhosts}
        showGhosts={showGhosts}
        query={query}
        search={search}
        searching={searching}
        onTau={setTau}
        onShowGhosts={setShowGhosts}
        onQuery={setQuery}
        onSelect={setSelected}
      />
      <Graph
        data={data}
        tau={threshold}
        showGhosts={showGhosts}
        selected={selected}
        matches={matches}
        onSelect={setSelected}
      />
      {selected && <Panel id={selected} onClose={() => setSelected(null)} />}
    </div>
  );
}
