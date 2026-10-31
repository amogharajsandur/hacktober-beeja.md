// Mirrors beeja/schema.py (the canonical model). The viewer is read-only.

export type AltStatus = "proposed" | "accepted" | "rejected" | "withdrawn" | "deferred" | "unresolved" | "superseded";

export interface Evidence {
  evidence_id: string;
  message_id: string;
  quote: string;
  source_url: string | null;
  author: string | null;
  created_at: string | null;
  start_offset: number | null;
  end_offset: number | null;
  verification: "exact" | "approximate" | "not_found" | "missing_message" | "unchecked";
  support: "supports" | "weak" | "does_not_support" | "unchecked";
}

export interface Reason {
  text: string;
  evidence_ids: string[];
  supported: boolean;
}

export interface Alternative {
  alternative_id: string;
  title: string;
  description: string;
  status: AltStatus;
  rejection_type: "explicit" | "inferred" | null;
  reasons: Reason[];
  supporting_evidence: string[];
  opposing_evidence: string[];
  status_evidence: string[];
  status_support: Evidence["support"];
  proponents: string[];
  confidence: "high" | "medium" | "low" | "uncertain";
  mentions: string[];
  notes: string[];
}

export interface CodeAnchor {
  kind: string;
  value: string;
  repository: string | null;
  note: string | null;
}

export interface DecisionRecord {
  id: string;
  question: string;
  summary: string;
  topic: string;
  status: string;
  alternatives: Alternative[];
  evidence: Evidence[];
  code_anchors: CodeAnchor[];
  source_threads: string[];
  decision_authority: string | null;
  confidence: string;
  supersedes: string[];
  superseded_by: string[];
  metadata: Record<string, unknown>;
}

export interface SourceMessage {
  author: string | null;
  role: string | null;
  created_at: string | null;
  source_url: string | null;
  body: string;
  reply_to: string | null;
}

export interface DecisionPayload {
  record: DecisionRecord;
  narrative: unknown[];
  narrative_generated: boolean;
  messages: Record<string, SourceMessage>;
}

export interface IndexEntry {
  id: string;
  question: string;
  topic: string;
  status: string;
  summary: string;
  alternatives: number;
  case: string;
}
