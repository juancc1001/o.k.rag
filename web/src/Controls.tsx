// τ slider, ghost toggle, search, and the legend for §8's encoding.
//
// The slider's floor is the lowest similarity graph.json actually contains, not
// 0: `okrag index` only emits pairs that already passed the configured τ and
// the top-k gate, so sliding below that would claim to show ghosts the file
// does not hold. Calibration happens upward from the configured value.

import type { Graph, SearchResult } from "./api";
import { typeColors } from "./encoding";

interface Props {
  data: Graph;
  tau: number;
  floor: number;
  visibleGhosts: number;
  showGhosts: boolean;
  query: string;
  search: SearchResult | null;
  searching: boolean;
  onTau: (tau: number) => void;
  onShowGhosts: (show: boolean) => void;
  onQuery: (query: string) => void;
  onSelect: (id: string) => void;
}

export function Controls(props: Props) {
  const { data, tau, floor, visibleGhosts, showGhosts, query, search, searching } = props;
  const colors = typeColors(data.nodes);

  return (
    <aside className="controls">
      <h1>okrag</h1>

      <section>
        <label className="field">
          <span>search</span>
          <input
            type="search"
            value={query}
            placeholder="a question, or a term"
            onChange={(event) => props.onQuery(event.target.value)}
          />
        </label>
        {searching && <p className="note">searching…</p>}
        {search && (
          <>
            <p className="note">
              answered by <strong>{search.path}</strong>
              {search.note ? ` — ${search.note}` : ""}
            </p>
            <ol className="hits">
              {search.results.map((hit) => (
                <li key={hit.concept_id}>
                  <button onClick={() => props.onSelect(hit.concept_id)}>
                    <span className="hit-title">{hit.title}</span>
                    <span className="hit-score">{hit.confidence.toFixed(3)}</span>
                  </button>
                </li>
              ))}
            </ol>
            {!search.results.length && <p className="note">nothing matched.</p>}
          </>
        )}
      </section>

      <section>
        <h2>ghost edges</h2>
        <p className="note">
          Similar pairs with no authored link — the work queue. This is what τ decides.
        </p>
        <label className="check">
          <input
            type="checkbox"
            checked={showGhosts}
            onChange={(event) => props.onShowGhosts(event.target.checked)}
          />
          <span>show ghost edges</span>
        </label>
        <label className="field">
          <span>
            τ <code>{tau.toFixed(3)}</code>
          </span>
          <input
            type="range"
            min={floor}
            max={1}
            step={0.001}
            value={tau}
            disabled={!showGhosts}
            onChange={(event) => props.onTau(Number(event.target.value))}
          />
        </label>
        <p className="note">
          <strong>{visibleGhosts}</strong> of {data.ghosts.length} ghost edge(s) at or above τ.
          The floor is {floor.toFixed(3)}, the lowest this graph holds.
        </p>
      </section>

      <section>
        <h2>bundle</h2>
        <dl className="stats">
          <dt>concepts</dt>
          <dd>{data.stats.concepts}</dd>
          <dt>authored links</dt>
          <dd>{data.stats.links}</dd>
          <dt>ghost edges</dt>
          <dd>{data.stats.ghosts}</dd>
          <dt>broken links</dt>
          <dd>{data.stats.broken_links}</dd>
        </dl>
      </section>

      <section>
        <h2>legend</h2>
        <ul className="legend">
          {[...colors].map(([type, color]) => (
            <li key={type}>
              <span className="swatch" style={{ background: color }} />
              {type}
            </li>
          ))}
        </ul>
        <ul className="legend">
          <li>
            <span className="swatch line" /> authored link
          </li>
          <li>
            <span className="swatch line ghost" /> ghost edge
          </li>
        </ul>
        <p className="note">
          Size is link degree. Border is the trust tier: none unverified, thin machine-confirmed,
          heavy human-reviewed. Faded means deprecated or past <code>stale_after</code>.
        </p>
      </section>
    </aside>
  );
}

/** The lowest similarity the graph holds, which is the slider's floor. */
export function ghostFloor(data: Graph): number {
  if (!data.ghosts.length) return 0.8;
  return Math.min(...data.ghosts.map((ghost) => ghost.similarity));
}
