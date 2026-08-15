// The side panel: selecting a node fetches GET /api/concept/{id} and shows the
// rendered body (§8). The frontmatter is shown first because it carries the
// provenance the body depends on — which is the same order AGENTS.md tells an
// agent to read a concept in.

import { useEffect, useState } from "react";

import { fetchConcept, type Concept } from "./api";

const SHOWN = ["type", "status", "description", "stale_after", "tags", "generated", "verified"];

export function Panel({ id, onClose }: { id: string; onClose: () => void }) {
  const [concept, setConcept] = useState<Concept | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    setConcept(null);
    setError(null);
    fetchConcept(id)
      .then((loaded) => live && setConcept(loaded))
      .catch((exc: Error) => live && setError(exc.message));
    return () => {
      live = false;
    };
  }, [id]);

  const frontmatter = concept?.frontmatter ?? {};
  const title = (frontmatter.title as string) ?? id;

  return (
    <aside className="panel">
      <header>
        <div>
          <h2>{title}</h2>
          <code>{id}</code>
        </div>
        <button className="close" onClick={onClose} aria-label="close">
          ×
        </button>
      </header>

      {error && <p className="error">{error}</p>}
      {!concept && !error && <p className="note">loading…</p>}

      {concept && (
        <>
          <dl className="frontmatter">
            {SHOWN.filter((key) => frontmatter[key] != null).map((key) => (
              <div key={key}>
                <dt>{key}</dt>
                <dd>{format(frontmatter[key])}</dd>
              </div>
            ))}
          </dl>
          <div className="body" dangerouslySetInnerHTML={{ __html: concept.html }} />
        </>
      )}
    </aside>
  );
}

function format(value: unknown): string {
  if (Array.isArray(value)) return value.map(format).join(", ");
  if (value && typeof value === "object") {
    return Object.entries(value)
      .map(([k, v]) => `${k}: ${v}`)
      .join(", ");
  }
  return String(value);
}
