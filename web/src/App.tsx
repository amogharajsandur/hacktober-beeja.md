import { useEffect, useMemo, useState } from "react";
import { PortableText } from "@portabletext/react";
import type { Alternative, DecisionPayload, Evidence, IndexEntry, SourceMessage } from "./types";
import { highlight, statusOrder } from "./logic";

type Load<T> = { state: "loading" } | { state: "error"; message: string } | { state: "ok"; data: T };

function useJson<T>(url: string | null): Load<T> {
  const [res, setRes] = useState<Load<T>>({ state: "loading" });
  useEffect(() => {
    if (!url) return;
    let alive = true;
    setRes({ state: "loading" });
    fetch(url)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`${r.status} ${r.statusText}`))))
      .then((data) => alive && setRes({ state: "ok", data }))
      .catch((e: Error) => alive && setRes({ state: "error", message: e.message }));
    return () => {
      alive = false;
    };
  }, [url]);
  return res;
}

function useHashRoute(): string {
  const [hash, setHash] = useState(window.location.hash);
  useEffect(() => {
    const on = () => setHash(window.location.hash);
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return hash;
}

export function App() {
  const hash = useHashRoute();
  const m = hash.match(/^#\/d\/([^/?#]+)(?:\/(ev-[0-9a-f]+))?/);
  return (
    <div className="page">
      <header className="top">
        <a href="#/" className="brand">beeja<span>.md</span></a>
        <span className="tag">decision dossier · read-only</span>
      </header>
      {m ? <DecisionView id={decodeURIComponent(m[1])} focus={m[2] ?? null} /> : <DecisionList />}
    </div>
  );
}

function Status({ s }: { s: string }) {
  return <span className={`badge st-${s}`}>{s.replace("_", " ")}</span>;
}

function DecisionList() {
  const idx = useJson<IndexEntry[]>("./data/index.json");
  const [q, setQ] = useState("");
  if (idx.state === "loading") return <p className="muted">Loading decisions…</p>;
  if (idx.state === "error")
    return (
      <p className="error">
        Could not load <code>data/index.json</code> ({idx.message}). Run <code>beeja export-web</code> first; the CLI
        (<code>beeja search</code>, <code>beeja get</code>) works without the viewer.
      </p>
    );
  const items = idx.data.filter((d) => (d.question + " " + d.topic + " " + d.summary).toLowerCase().includes(q.toLowerCase()));
  return (
    <main>
      <h1>Recovered decisions</h1>
      <input className="search" placeholder="Filter by question or topic" value={q} onChange={(e) => setQ(e.target.value)} />
      {items.length === 0 && <p className="muted">No decisions match “{q}”.</p>}
      <ul className="cards">
        {items.map((d) => (
          <li key={d.id} className="card">
            <a href={`#/d/${encodeURIComponent(d.id)}`}>
              <div className="row">
                <Status s={d.status} />
                <span className="muted small">{d.topic}</span>
              </div>
              <h2>{d.question}</h2>
              <p className="muted">{d.summary}</p>
            </a>
          </li>
        ))}
      </ul>
    </main>
  );
}

function DecisionView({ id, focus }: { id: string; focus: string | null }) {
  const res = useJson<DecisionPayload>(`./data/${encodeURIComponent(id)}.json`);
  const [selected, setSelected] = useState<string | null>(focus);
  useEffect(() => setSelected(focus), [focus]);
  if (res.state === "loading") return <p className="muted">Loading decision…</p>;
  if (res.state === "error") return <p className="error">Decision {id} could not be loaded ({res.message}).</p>;
  const { record, narrative, narrative_generated, messages } = res.data;
  const ev = Object.fromEntries(record.evidence.map((e) => [e.evidence_id, e]));
  const alts = [...record.alternatives].sort((a, b) => statusOrder(a.status) - statusOrder(b.status));
  const meta = record.metadata as Record<string, string | number | boolean>;
  return (
    <main className="dossier">
      <a href="#/" className="muted small">← all decisions</a>
      <div className="row">
        <Status s={record.status} />
        <span className="muted small">{record.topic}</span>
      </div>
      <h1>{record.question}</h1>
      <div className="notice">
        AI-extracted by <code>{String(meta.model)}</code> ({String(meta.extractor)}) — not human-reviewed. Outcomes are
        only shown as decided when a cited passage was found verbatim in the source and passed the support check.
        {" "}{String(meta.evidence_verified)}/{String(meta.evidence_total)} quotes verified.
      </div>
      {narrative.length > 0 && (
        <section className="narrative">
          <PortableText value={narrative as never} />
          {narrative_generated && <p className="muted small">Narrative generated from the structured record.</p>}
        </section>
      )}
      <div className="grid">
        <section>
          <h2>Alternatives</h2>
          {alts.map((a) => (
            <AltCard key={a.alternative_id} a={a} ev={ev} onPick={setSelected} selected={selected} />
          ))}
          {record.code_anchors.length > 0 && (
            <>
              <h2>Code anchors</h2>
              <p className="muted small">Paths, symbols and references the discussion itself mentions.</p>
              <ul className="anchors">
                {record.code_anchors.map((c) => (
                  <li key={c.kind + c.value}>
                    <span className="kind">{c.kind}</span> <code>{c.value}</code> <span className="muted small">{c.note}</span>
                  </li>
                ))}
              </ul>
            </>
          )}
          <h2>Sources</h2>
          <ul>
            {record.source_threads.map((s) => (
              <li key={s}>{s.startsWith("http") ? <a href={s}>{s}</a> : s}</li>
            ))}
          </ul>
        </section>
        <aside>
          <h2>Evidence</h2>
          {selected && ev[selected] ? (
            <EvidencePanel e={ev[selected]} msg={messages[ev[selected].message_id]} />
          ) : (
            <p className="muted">Select a quote to see it highlighted in its source message.</p>
          )}
        </aside>
      </div>
    </main>
  );
}

function EvRef({ id, ev, onPick, selected, support }: { id: string; ev: Record<string, Evidence>; onPick: (s: string) => void; selected: string | null; support?: Evidence["support"] }) {
  const e = ev[id];
  if (!e) return null;
  const ok = e.verification === "exact" || e.verification === "approximate";
  // an outcome chip shows the verdict for *this* claim; one passage may back several claims
  const sup = support ?? e.support;
  return (
    <button className={`evref ${ok ? "" : "bad"} ${selected === id ? "sel" : ""}`} onClick={() => onPick(id)} title={e.quote}>
      {e.message_id} · {e.verification}
      {sup !== "unchecked" ? ` · ${sup.replace(/_/g, " ")}` : ""}
    </button>
  );
}

function AltCard(props: { a: Alternative; ev: Record<string, Evidence>; onPick: (s: string) => void; selected: string | null }) {
  const { a } = props;
  return (
    <article className={`alt alt-${a.status}`}>
      <div className="row">
        <Status s={a.status} />
        {a.rejection_type && <span className="badge ghost">{a.rejection_type}</span>}
        <span className="badge ghost">confidence: {a.confidence}</span>
      </div>
      <h3>{a.title}</h3>
      {a.description && <p>{a.description}</p>}
      {a.status_evidence.length > 0 && (
        <p className="small">
          Outcome evidence: {a.status_evidence.map((i) => <EvRef key={i} id={i} {...props} support={a.status_support} />)}
        </p>
      )}
      {a.reasons.length > 0 && (
        <ul className="reasons">
          {a.reasons.map((r, i) => (
            <li key={i} className={r.supported ? "" : "unsup"}>
              {r.text} {r.evidence_ids.map((id) => <EvRef key={id} id={id} {...props} />)}
              {!r.supported && <span className="muted small"> (unverified)</span>}
            </li>
          ))}
        </ul>
      )}
      {a.notes.map((n) => (
        <p key={n} className="note small">⚠ {n}</p>
      ))}
    </article>
  );
}

function EvidencePanel({ e, msg }: { e: Evidence; msg: SourceMessage | undefined }) {
  const parts = useMemo(() => (msg ? highlight(msg.body, e.start_offset, e.end_offset) : null), [msg, e]);
  return (
    <div className="evpanel">
      <p className="small">
        <strong>{e.author ?? "author unknown (quoted reply)"}</strong> · {e.message_id}
        {e.created_at && <> · {e.created_at.slice(0, 10)}</>}
        {e.source_url && (
          <>
            {" "}· <a href={e.source_url}>original post ↗</a>
          </>
        )}
      </p>
      <p className="small">
        Verification: <strong>{e.verification}</strong> · Support: <strong>{e.support.replace(/_/g, " ")}</strong>
      </p>
      {parts ? (
        <pre className="msg">
          {parts.before}
          <mark>{parts.match}</mark>
          {parts.after}
        </pre>
      ) : (
        <blockquote className={e.verification === "exact" || e.verification === "approximate" ? "" : "bad"}>{e.quote}</blockquote>
      )}
      {(e.verification === "not_found" || e.verification === "missing_message") && (
        <p className="error small">This passage was not found in the cited message and is not used as proof.</p>
      )}
    </div>
  );
}
