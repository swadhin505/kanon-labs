export type EventSummary = {
  step: number;
  kind: "message" | "tool_call";
  role: "user" | "agent" | null;
  content: string | null;
  confirms: string[];
  operation: string | null;
  args: Record<string, unknown>;
  result: unknown;
  error: string | null;
  deterministic: boolean | null;
};

export type StateChange = {
  resource: string;
  id: string;
  op: "created" | "changed" | "deleted";
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  fields: Record<string, [unknown, unknown]>;
};

export type InvariantResult = {
  name: string;
  description: string;
  policy: string | null;
  passed: boolean;
  violations: { message: string; step: number | null }[];
};

export type TrialDetail = {
  passed: boolean;
  state_ok: boolean;
  calls_ok: boolean;
  invariants_ok: boolean;
  interaction_ok?: boolean;
  temporal_ok?: boolean;
  outcome?: string | null;
  reasons: string[];
  events: EventSummary[];
  changes: StateChange[];
  invariants: InvariantResult[];
};

export type StorySummary = {
  id: string;
  intent: string;
  policy: string;
  persona: string;
  labels?: Record<string, string>;
  trials: number;
  successes: number;
  trivially_passed: boolean;
  invariants: number;
  reasons: string[];
  trial_details: TrialDetail[];
};

export type RunReport = {
  domain: string;
  agent: string;
  stories: StorySummary[];
  uncovered: Record<string, number>;
  model_calls: number;
};

export type SliceResult = {
  id: string;
  label: string;
  intent: string;
  policy: string;
  persona: string;
  labels: Record<string, string>;
  before: number | null;
  after: number | null;
  passAtOne: number;
  delta: number;
  status: "regressed" | "improved" | "flat" | "new" | "gone";
};
