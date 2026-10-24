export type RoleId = 'CFO' | 'COO';
export type Company = { company_id: string; display_name: string; category: 'TRADE' | 'TRANSPORT'; profile_version: string };
export type InsightSummary = {
  insight_id: string; company_id: string; role_id: RoleId; event_id: string;
  headline: string; summary: string; created_at: string; bank_position_status: string;
  synthetic: boolean; exposure_status: 'unknown' | 'conditional' | 'confirmed' | 'contradicted';
};
export type Source = {
  source_id: string; kind: string; title: string; uri: string | null;
  published_at: string | null; retrieved_at: string; document_date: string | null;
  locator: string; synthetic: boolean; trust_status: string;
};
export type InsightBlock = {
  block_id: string; kind: 'observed_fact' | 'bank_position' | 'ai_inference';
  text: string; evidence_refs: string[]; assumptions: string[]; unknowns: string[];
};
export type SourceRef = Pick<Source, 'source_id' | 'locator'> & Partial<Omit<Source, 'source_id' | 'locator'>>;
export type Question = { question_id: string; text: string; kind: 'exposure_confirmation'; options?: string[] };
export type Insight = InsightSummary & { blocks: InsightBlock[]; sources: SourceRef[]; unknowns: string[]; question: Question | null };
export type DialogueResult = { message_id: string; insight_id: string; status: 'succeeded' | 'failed' | 'unknown_outcome'; blocks: InsightBlock[]; sources: SourceRef[]; unknowns: string[]; question: Question | null; input_version: string; error_code: string | null };
export type AnalysisRun = {
  run_id: string; company_id: string; role_id: RoleId; input_version: string;
  input_snapshot_id: string; status: 'queued' | 'running' | 'succeeded' | 'failed' | 'unknown_outcome';
  stage: string; created_at: string; updated_at: string; result_ids: string[];
  error_code: string | null; execution_mode: 'deterministic_demo' | 'agentic_fixture' | 'mvp_offline' | 'broker_live';
};
type ApiError = { error?: { code?: string; message?: string; retryable?: boolean; run_id?: string | null } };

export class RequestError extends Error {
  constructor(message: string, readonly status: number, readonly code?: string) { super(message); }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try { response = await fetch(`/api/v1${path}`, init); }
  catch { throw new RequestError('Не удалось связаться с локальным сервисом. Проверьте, запущен ли backend.', 0); }
  const body = await response.json().catch(() => ({})) as T & ApiError;
  if (!response.ok) throw new RequestError(body.error?.message ?? `Ошибка запроса (${response.status})`, response.status, body.error?.code);
  return body;
}

export const api = {
  companies: (signal?: AbortSignal) => request<{ items: Company[] }>('/companies', { signal }),
  feed: (company: string, role: RoleId, signal?: AbortSignal) => request<{ items: InsightSummary[]; next_cursor: string | null }>(`/companies/${encodeURIComponent(company)}/feed?role_id=${role}`, { signal }),
  insight: (id: string, signal?: AbortSignal) => request<Insight>(`/insights/${encodeURIComponent(id)}`, { signal }),
  run: (company: string, role: RoleId, signal?: AbortSignal, idempotencyKey = crypto.randomUUID()) => request<AnalysisRun>(`/companies/${encodeURIComponent(company)}/analysis-runs`, {
    method: 'POST', signal, headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ role_id: role, idempotency_key: idempotencyKey }),
  }),
  getRun: (id: string, signal?: AbortSignal) => request<AnalysisRun>(`/analysis-runs/${encodeURIComponent(id)}`, { signal }),
  answer: (id: string, questionId: string, answer: string, key: string, signal?: AbortSignal) => request<DialogueResult>(`/insights/${encodeURIComponent(id)}/questions`, {
    method: 'POST', signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ question_id: questionId, answer, idempotency_key: key }),
  }),
  message: (id: string, text: string, key: string, signal?: AbortSignal) => request<DialogueResult>(`/insights/${encodeURIComponent(id)}/messages`, {
    method: 'POST', signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text, idempotency_key: key }),
  }),
};
