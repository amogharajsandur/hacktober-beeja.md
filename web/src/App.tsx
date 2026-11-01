import { useEffect, useMemo, useRef, useState } from "react";
import { PortableText } from "@portabletext/react";
import type { Alternative, DecisionPayload, Evidence, IndexEntry, SourceMessage } from "./types";
import { DECIDED, countBy, hostOf, hue, initials, parseBlocks, segments, statusOrder } from "./logic";

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

const ICON: Record<string, string> = {
  accepted: "✔", rejected: "✕", withdrawn: "↩", deferred: "⏸", superseded: "⇢", unresolved: "?", proposed: "•",
  decided: "✔", partially_decided: "◐",
};
const LABEL: Record<string, string> = { partially_decided: "partially decided" };

export function App() {
  const hash = useHashRoute();
  const m = hash.match(/^#\/d\/([^/?#]+)(?:\/(ev-[0-9a-f]+))?/);
  const page = m?.[1];
  useEffect(() => {
    window.scrollTo(0, 0);
  }, [page]);
  return (
    <div className="shell">
      <header className="top">
        <a href="#/" className="brand">
          <span className="logo">b</span>beeja<span className="dim">.md</span>
        </a>
        <span className="tagline">what was decided, what was rejected, why — and where's the proof</span>
      </header>
      {m ? <DecisionView id={decodeURIComponent(m[1])} focus={m[2] ?? null} /> : <DecisionList />}
      <footer className="foot">Read-only dossier · records extracted by an open-weight model and verified against the source text</footer>
    </div>
  );
}

function Pill({ s, small }: { s: string; small?: boolean }) {
  return (
    <span className={`pill st-${s} ${small ? "sm" : ""}`}>
      <span className="ico">{ICON[s] ?? "•"}</span>
      {LABEL[s] ?? s}
    </span>
  );
}

function Favicon({ url, size = 16 }: { url: string | null | undefined; size?: number }) {
  const host = hostOf(url);
  // try the site's own favicon, then a favicon service, then a neutral glyph
  const [attempt, setAttempt] = useState(0);
  const srcs = host ? [`https://${host}/favicon.ico`, `https://www.google.com/s2/favicons?domain=${host}&sz=64`] : [];
  if (attempt >= srcs.length) return <span className="globe" style={{ width: size, height: size }}>◎</span>;
  return (
    <img className="favicon" src={srcs[attempt]} width={size} height={size} alt="" onError={() => setAttempt((n) => n + 1)} />
  );
}

function Tiles({ counts, verified, total }: { counts: Record<string, number>; verified?: number; total?: number }) {
  const order = ["accepted", "rejected", "withdrawn", "deferred", "superseded", "unresolved", "proposed"];
  return (
    <div className="tiles">
      {order.filter((s) => counts[s]).map((s) => (
        <div key={s} className={`tile st-${s}`}>
          <span className="num">{counts[s]}</span>
          <span className="lbl">{s}</span>
        </div>
      ))}
      {total ? (
        <div className="tile verify">
          <span className="num">
            {verified}
            <span className="of">/{total}</span>
          </span>
          <span className="lbl">quotes verified</span>
          <span className="bar">
            <span style={{ width: `${Math.round(((verified ?? 0) / total) * 100)}%` }} />
          </span>
        </div>
      ) : null}
    </div>
  );
}

// ------------------------------------------------------------------ list

// ------------------------------------------------------------------ GitHub import (needs `beeja serve`)

type Job = {
  state: "running" | "done" | "error";
  progress: string[];
  error?: string;
  kind?: string;
  decision_id?: string;
  result?: { decision_id: string; summary: string; messages: number; archived_previous: string | null };
};
type Status = { imports: boolean; model: string | null; model_error: string | null; github_auth: boolean };

const HINTS: Record<string, string> = {
  invalid_url: "Paste a PEP (https://peps.python.org/pep-0758/ or “PEP 758”), a Discourse thread, or a GitHub issue/PR URL.",
  no_discussion: "This PEP was discussed on a mailing list, which is not supported — paste its discuss.python.org thread URL if there is one.",
  not_found: "Check the number, or set GITHUB_TOKEN in .env for private repositories.",
  rate_limited: "Add GITHUB_TOKEN to .env (or run `gh auth login`), restart `make viewer`, and retry.",
  network: "Check your internet connection.",
  empty: "Nothing to analyse yet — come back when the thread has a discussion.",
  model_unreachable: "Start your model server, or fix BEEJA_PROVIDER / BEEJA_MODEL / BEEJA_BASE_URL in .env.",
  model: "The model failed. Try again, or configure a different model in .env.",
};

function ImportPanel({ onImported }: { onImported: () => void }) {
  const [status, setStatus] = useState<Status | null | "offline">(null);
  const [url, setUrl] = useState("");
  const [job, setJob] = useState<Job | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    fetch("/api/status")
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then(setStatus)
      .catch(() => setStatus("offline"));
  }, []);

  const start = async (reimport = false) => {
    setBusy(true);
    setJob({ state: "running", progress: ["Starting…"] });
    try {
      const r = await fetch("/api/import", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: url.trim(), reimport }),
      });
      const body = await r.json();
      if (r.status !== 202) {
        setJob({ state: "error", progress: [], error: body.error, kind: body.kind, decision_id: body.decision_id });
        setBusy(false);
        return;
      }
      const poll = async () => {
        const j: Job = await (await fetch(`/api/jobs/${body.job}`)).json();
        setJob(j);
        if (j.state === "running") setTimeout(poll, 1000);
        else {
          setBusy(false);
          if (j.state === "done") onImported();
        }
      };
      poll();
    } catch {
      setJob({ state: "error", progress: [], error: "Lost contact with the local server.", kind: "network" });
      setBusy(false);
    }
  };

  if (status === null) return null;
  if (status === "offline")
    return (
      <div className="card importer off">
        <strong>Import a discussion</strong>
        <p className="fine">
          This copy of the viewer is static. Start it with <code>make viewer</code> to paste a PEP, Discourse thread or
          GitHub issue/PR URL and import it here.
        </p>
      </div>
    );
  return (
    <div className="card importer">
      <div className="imp-head">
        <strong>Import a discussion</strong>
        <span className="fine">
          model <code>{status.model ?? "not configured"}</code> · GitHub {status.github_auth ? "authenticated" : "anonymous (60 req/h)"}
        </span>
      </div>
      {status.model_error && <p className="callout bad">Model not configured: {status.model_error}. Edit .env.</p>}
      <form
        className="imp-row"
        onSubmit={(e) => {
          e.preventDefault();
          if (!busy && url.trim()) start(false);
        }}
      >
        <input placeholder="https://peps.python.org/pep-0758/  ·  https://discuss.python.org/t/…  ·  https://github.com/o/r/issues/1" value={url} onChange={(e) => setUrl(e.target.value)} disabled={busy} />
        <button className="primary" disabled={busy || !url.trim()}>
          {busy ? "Importing…" : "Import"}
        </button>
      </form>
      {job && (
        <div className={`job ${job.state}`}>
          {job.progress.length === 0 && job.state === "running" && <p className="fine">Working…</p>}
          {job.progress.length > 0 && (
            <ol className="steps">
              {job.progress.map((p, i) => (
                <li key={i} className={i === job.progress.length - 1 && job.state === "running" ? "now" : ""}>
                  {p}
                </li>
              ))}
            </ol>
          )}
          {job.state === "done" && job.result && (
            <div className="done">
              <span>✔ {job.result.summary}</span>
              {job.result.archived_previous && <span className="fine">Previous version kept in {job.result.archived_previous}</span>}
              <a className="primary" href={`#/d/${encodeURIComponent(job.result.decision_id)}`}>Open dossier →</a>
            </div>
          )}
          {job.state === "error" && (
            <div className="err">
              <strong>✕ {job.error}</strong>
              {job.kind && HINTS[job.kind] && <span className="fine">{HINTS[job.kind]}</span>}
              {job.kind === "exists" && job.decision_id && (
                <div className="imp-actions">
                  <a className="primary" href={`#/d/${encodeURIComponent(job.decision_id)}`}>Open existing</a>
                  <button onClick={() => start(true)}>Re-import (keep old version in history)</button>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function DecisionList() {
  const [version, setVersion] = useState(0);
  const idx = useJson<IndexEntry[]>(`./data/index.json?v=${version}`);
  const [q, setQ] = useState("");
  const [last, setLast] = useState<IndexEntry[] | null>(null);
  useEffect(() => {
    if (idx.state === "ok") setLast(idx.data);
  }, [idx]);
  const importer = <ImportPanel onImported={() => setVersion((v) => v + 1)} />;
  const data = idx.state === "ok" ? idx.data : last; // keep showing the old list while reloading
  if (!data && idx.state === "loading") return <Skeleton />;
  if (!data)
    return (
      <main className="list">
        {importer}
        <div className="empty error">
          <h2>No decision data yet</h2>
          <p>
            Could not load <code>data/index.json</code> ({idx.state === "error" ? idx.message : ""}). Run{" "}
            <code>make viewer</code>. Everything is also available from the CLI: <code>beeja search</code>.
          </p>
        </div>
      </main>
    );
  const items = data.filter((d) => (d.question + " " + d.topic + " " + d.summary).toLowerCase().includes(q.toLowerCase()));
  return (
    <main className="list">
      <section className="hero">
        <h1>Recovered decisions</h1>
        <p className="lead">
          Each dossier lists every alternative that was discussed, what happened to it, and the exact sentences that
          prove it.
        </p>
        <div className="searchbox">
          <span>⌕</span>
          <input placeholder="Filter by question or topic…" value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
      </section>
      {importer}
      {items.length === 0 && <div className="empty">No decisions match “{q}”.</div>}
      <div className="grid-cards">
        {items.map((d) => (
          <a key={d.id} className="dcard" href={`#/d/${encodeURIComponent(d.id)}`}>
            <div className="row">
              <Pill s={d.status} small />
              <span className="source">
                <Favicon url={d.source} size={14} /> {hostOf(d.source) ?? "local file"}
              </span>
            </div>
            <h2>{d.question}</h2>
            <p className="topic">{d.topic}</p>
            {d.counts && <Tiles counts={d.counts} verified={d.evidence_verified} total={d.evidence_total} />}
            <span className="open">Open dossier →</span>
          </a>
        ))}
      </div>
    </main>
  );
}

function Skeleton() {
  return (
    <div className="skeleton">
      <div />
      <div />
      <div />
    </div>
  );
}

// ------------------------------------------------------------------ dossier

type Filter = "all" | "decided" | "open";

function DecisionView({ id, focus }: { id: string; focus: string | null }) {
  const res = useJson<DecisionPayload>(`./data/${encodeURIComponent(id)}.json`);
  const [selected, setSelected] = useState<string | null>(focus);
  const [filter, setFilter] = useState<Filter>("all");
  useEffect(() => {
    setSelected(focus);
  }, [focus]);
  if (res.state === "loading") return <Skeleton />;
  if (res.state === "error")
    return (
      <div className="empty error">
        Decision <code>{id}</code> could not be loaded ({res.message}). <a href="#/">Back to all decisions</a>
      </div>
    );
  const { record, narrative, narrative_generated, messages } = res.data;
  const ev = Object.fromEntries(record.evidence.map((e) => [e.evidence_id, e]));
  const counts = countBy(record.alternatives.map((a) => a.status));
  const meta = record.metadata as Record<string, string | number | boolean>;
  const decidedN = record.alternatives.filter((a) => DECIDED.has(a.status)).length;
  const alts = [...record.alternatives]
    .sort((a, b) => statusOrder(a.status) - statusOrder(b.status))
    .filter((a) => filter === "all" || (filter === "decided") === DECIDED.has(a.status));
  const source = record.source_threads.find((s) => s.startsWith("http"));
  const tabs: [Filter, number][] = [
    ["all", record.alternatives.length],
    ["decided", decidedN],
    ["open", record.alternatives.length - decidedN],
  ];
  return (
    <main className="dossier">
      <a href="#/" className="back">← All decisions</a>
      <section className="hero">
        <div className="row">
          <Pill s={record.status} />
          {source && (
            <a className="source link" href={source} target="_blank" rel="noreferrer">
              <Favicon url={source} /> {hostOf(source)} ↗
            </a>
          )}
        </div>
        <h1>{record.question}</h1>
        <p className="topic">{record.topic}</p>
        <Tiles counts={counts} verified={Number(meta.evidence_verified)} total={Number(meta.evidence_total)} />
        <div className="ai-note">
          <span className="badge-ai">AI-extracted</span>
          <span>
            by <code>{String(meta.model)}</code> from {String(meta.messages)} messages · not human-reviewed. An outcome
            is shown only when its quote was found verbatim in the source and passed a support check.
          </span>
        </div>
      </section>

      {narrative.length > 0 && (
        <section className="card narrative">
          <h3 className="eyebrow">Summary</h3>
          <PortableText value={narrative as never} />
          {narrative_generated && <p className="fine">Generated from the structured record.</p>}
        </section>
      )}

      <div className="layout">
        <section>
          <div className="toolbar">
            <h2>Alternatives</h2>
            <div className="tabs" role="tablist">
              {tabs.map(([f, n]) => (
                <button key={f} role="tab" aria-selected={filter === f} className={filter === f ? "on" : ""} onClick={() => setFilter(f)}>
                  {f} <span className="n">{n}</span>
                </button>
              ))}
            </div>
          </div>
          {alts.length === 0 && <div className="empty">Nothing in this view.</div>}
          {alts.map((a) => (
            <AltCard key={a.alternative_id} a={a} ev={ev} messages={messages} onPick={setSelected} selected={selected} />
          ))}

          {record.code_anchors.length > 0 && (
            <section className="card">
              <h3 className="eyebrow">Code anchors</h3>
              <p className="fine">Files, symbols and references the discussion itself mentions — used by <code>beeja check-diff</code>.</p>
              <div className="chips">
                {record.code_anchors.map((c) => (
                  <span key={c.kind + c.value} className={`chip k-${c.kind}`} title={c.note ?? ""}>
                    <span className="k">{c.kind}</span>
                    <code>{c.value}</code>
                  </span>
                ))}
              </div>
            </section>
          )}
        </section>

        <aside>
          {selected && ev[selected] ? (
            <PostCard e={ev[selected]} msg={messages[ev[selected].message_id]} onClose={() => setSelected(null)} />
          ) : (
            <div className="card hint">
              <div className="hint-ico">❝</div>
              <h3>Inspect the proof</h3>
              <p>Click any quote or post chip to open the original message with the cited passage highlighted.</p>
            </div>
          )}
        </aside>
      </div>
    </main>
  );
}

function verifiedOk(e: Evidence) {
  return e.verification === "exact" || e.verification === "approximate";
}

type ChipProps = {
  id: string;
  ev: Record<string, Evidence>;
  messages: Record<string, SourceMessage>;
  onPick: (s: string) => void;
  selected: string | null;
  support?: Evidence["support"];
};

function EvChip(props: ChipProps) {
  const e = props.ev[props.id];
  if (!e) return null;
  const ok = verifiedOk(e);
  const sup = props.support ?? e.support;
  const author = props.messages[e.message_id]?.author ?? e.author;
  return (
    <button
      className={`evchip ${ok ? "ok" : "bad"} ${props.selected === props.id ? "sel" : ""}`}
      onClick={() => props.onPick(props.id)}
      title={ok ? e.quote : "Not found in the cited message — not used as proof"}
    >
      <span className="av sm" style={{ ["--h" as string]: hue(author) }}>{initials(author)}</span>
      {e.message_id}
      <span className={`dot v-${e.verification}`} />
      {sup !== "unchecked" && <span className={`sup s-${sup}`}>{sup.replace(/_/g, " ")}</span>}
    </button>
  );
}

function AltCard(props: {
  a: Alternative;
  ev: Record<string, Evidence>;
  messages: Record<string, SourceMessage>;
  onPick: (s: string) => void;
  selected: string | null;
}) {
  const { a, ev, messages, onPick, selected } = props;
  const chip = { ev, messages, onPick, selected };
  const outcome = a.status_evidence.map((i) => ev[i]).find((e) => e && verifiedOk(e));
  const conf = { high: 3, medium: 2, low: 1, uncertain: 0 }[a.confidence] ?? 0;
  return (
    <article className={`alt st-border-${a.status}`}>
      <header>
        <Pill s={a.status} small />
        {a.rejection_type && <span className="tag">{a.rejection_type}</span>}
        <span className="conf" title={`confidence: ${a.confidence}`}>
          {[1, 2, 3].map((i) => (
            <i key={i} className={i <= conf ? "on" : ""} />
          ))}
          {a.confidence}
        </span>
      </header>
      <h3>{a.title}</h3>
      {a.description && a.description !== a.title && <p className="desc">{a.description}</p>}

      {outcome && DECIDED.has(a.status) && (
        <button className={`outcome ${selected === outcome.evidence_id ? "sel" : ""}`} onClick={() => onPick(outcome.evidence_id)}>
          <span className="q">“{outcome.quote}”</span>
          <span className="by">
            — {messages[outcome.message_id]?.author ?? outcome.author ?? "unknown"}, {outcome.message_id} · view in post →
          </span>
        </button>
      )}

      {a.reasons.length > 0 && (
        <div className="reasons">
          <h4>{a.status === "accepted" ? "Why it was chosen" : DECIDED.has(a.status) ? "Why not" : "Arguments raised"}</h4>
          <ul>
            {a.reasons.map((r, i) => (
              <li key={i} className={r.supported ? "" : "unsup"}>
                <span>{r.text}</span>
                <span className="evs">
                  {r.evidence_ids.map((id) => (
                    <EvChip key={id} id={id} {...chip} />
                  ))}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {a.status_evidence.length > 0 && !(outcome && DECIDED.has(a.status)) && (
        <p className="fine cited">
          Cited outcome{" "}
          {a.status_evidence.map((i) => (
            <EvChip key={i} id={i} {...chip} support={a.status_support} />
          ))}
        </p>
      )}
      {a.notes.map((n) => (
        <p key={n} className="callout">⚠ {n}</p>
      ))}
    </article>
  );
}

function PostCard({ e, msg, onClose }: { e: Evidence; msg: SourceMessage | undefined; onClose: () => void }) {
  const markRef = useRef<HTMLElement | null>(null);
  useEffect(() => {
    markRef.current?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [e.evidence_id]);
  const author = msg?.author ?? e.author;
  const blocks = useMemo(() => (msg ? parseBlocks(msg.body) : []), [msg]);
  const ok = verifiedOk(e);
  const url = msg?.source_url ?? e.source_url;
  const date = msg?.created_at ?? e.created_at;
  let first = true;
  const render = (s: number, en: number) =>
    segments(msg!.body, s, en, ok ? e.start_offset : null, ok ? e.end_offset : null).map((seg, i) => {
      if (!seg.mark) return <span key={i}>{seg.text}</span>;
      const isFirst = first;
      first = false;
      return (
        <mark key={i} ref={isFirst ? (el) => { markRef.current = el; } : undefined}>
          {seg.text}
        </mark>
      );
    });
  return (
    <div className="card post">
      <div className="post-head">
        <span className="av" style={{ ["--h" as string]: hue(author) }}>{initials(author)}</span>
        <div className="who">
          <strong>{author ?? "author unknown (quoted reply)"}</strong>
          <span className="fine">
            {msg?.role && <span className="role">{msg.role}</span>}
            {e.message_id}
            {msg?.reply_to && <> · replying to {msg.reply_to}</>}
            {date && <> · {date.slice(0, 10)}</>}
          </span>
        </div>
        <button className="x" onClick={onClose} aria-label="Close">×</button>
      </div>
      <div className="badges">
        <span className={`vbadge v-${e.verification}`}>{ok ? "✔" : "✕"} quote {e.verification.replace("_", " ")}</span>
        {e.support !== "unchecked" && <span className={`vbadge s-${e.support}`}>support: {e.support.replace(/_/g, " ")}</span>}
      </div>
      {msg ? (
        <div className="md">
          {blocks.map((b, i) =>
            b.kind === "code" ? (
              <pre key={i}>
                {b.lines.map((l, j) => (
                  <span key={j}>
                    {render(l.s, l.e)}
                    {"\n"}
                  </span>
                ))}
              </pre>
            ) : b.kind === "quote" ? (
              <blockquote key={i}>
                {b.lines.map((l, j) => (
                  <span key={j}>{render(l.s, l.e)} </span>
                ))}
              </blockquote>
            ) : b.kind === "list" ? (
              <ul key={i}>
                {b.lines.map((l, j) => (
                  <li key={j}>{render(l.s, l.e)}</li>
                ))}
              </ul>
            ) : (
              <p key={i}>
                {b.lines.map((l, j) => (
                  <span key={j}>{render(l.s, l.e)} </span>
                ))}
              </p>
            ),
          )}
        </div>
      ) : (
        <blockquote className={ok ? "" : "bad"}>{e.quote}</blockquote>
      )}
      {!ok && <p className="callout bad">This passage was not found in the cited message, so it is never used as proof.</p>}
      {url && (
        <a className="open-src" href={url} target="_blank" rel="noreferrer">
          <Favicon url={url} /> Open original on {hostOf(url)} ↗
        </a>
      )}
    </div>
  );
}
