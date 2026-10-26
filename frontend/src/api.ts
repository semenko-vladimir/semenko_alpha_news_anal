export type RoleId = 'CFO' | 'COO';
export type Company = { company_id: string; display_name: string; category: string; profile_version: string;
  profile_origin?: 'public' | 'provided'; sector_id?: string; sector_label?: string; as_of?: string | null; checked_at?: string | null; eligibility_status?: string };
export type CompanyProfile = Company & { legal_name: string | null; entity_scope: string | null; activity: string | null;
  metrics: {name: string; value: number; unit: string; currency: string | null; period: string; scope: string; source_id: string}[];
  unknowns: string[]; sources: SourceRef[] };
export type DataStatus = { news_count: number; source_errors: number; latest_successful_fetch: string | null;
  cbr?: {last_attempt: {status: string} | null; last_success_at: string | null};
  execution_mode: AnalysisRun['execution_mode']; official_sources?: {source_id: string; status: string; last_success_at: string | null}[];
  local_model?: {provider?: string; name: string; display_name: string; prompt_version?: string; hackathon_allowlisted: boolean} | null;
  live_operations: {blocked: boolean; stop_code: string | null; daily_attempt_limit: number | null; remaining_attempts: number | null; reset_time_msk: string | null} | null };
export type InsightSummary = {
  insight_id: string; company_id: string; role_id: RoleId; event_id: string;
  headline: string; summary: string; created_at: string; bank_position_status: string;
  synthetic: boolean; exposure_status: 'unknown' | 'conditional' | 'confirmed' | 'contradicted';
  event_published_at?: string | null;
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
  error_code: string | null; execution_mode: 'deterministic_demo' | 'agentic_fixture' | 'mvp_offline' | 'broker_live' | 'local_ollama';
};
export type RunTrace = { run_id: string; company_id: string; role_id: RoleId; status: string; execution_mode: AnalysisRun['execution_mode']; trace: { stage: string; status: string; attempt?: number; reason?: string; reasons?: string[]; sources?: { source_id: string; kind: string; date: string | null }[] }[]; features: string[] };
type ApiError = { error?: { code?: string; message?: string; retryable?: boolean; run_id?: string | null } };

export class RequestError extends Error {
  constructor(message: string, readonly status: number, readonly code?: string) { super(message); }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try { response = await fetch(`/api/v1${path}`, init); }
  catch { throw new RequestError('Не удалось связаться с сервисом. Проверьте подключение и состояние сервера.', 0); }
  const body = await response.json().catch(() => ({})) as T & ApiError;
  if (!response.ok) throw new RequestError(body.error?.message ?? `Ошибка запроса (${response.status})`, response.status, body.error?.code);
  return body;
}

export const api = {
  companies: (signal?: AbortSignal) => request<{ items: Company[] }>('/companies', { signal }),
  profile: (company: string, signal?: AbortSignal) => request<CompanyProfile>(`/companies/${encodeURIComponent(company)}/profile`, {signal}),
  dataStatus: (signal?: AbortSignal) => request<DataStatus>('/data-status', {signal}),
  feed: (company: string, role: RoleId, signal?: AbortSignal) => request<{ items: InsightSummary[]; next_cursor: string | null }>(`/companies/${encodeURIComponent(company)}/feed?role_id=${role}`, { signal }),
  insight: (id: string, signal?: AbortSignal) => request<Insight>(`/insights/${encodeURIComponent(id)}`, { signal }),
  run: (company: string, role: RoleId, signal?: AbortSignal, idempotencyKey = crypto.randomUUID()) => request<AnalysisRun>(`/companies/${encodeURIComponent(company)}/analysis-runs`, {
    method: 'POST', signal, headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ role_id: role, idempotency_key: idempotencyKey }),
  }),
  getRun: (id: string, signal?: AbortSignal) => request<AnalysisRun>(`/analysis-runs/${encodeURIComponent(id)}`, { signal }),
  trace: (id: string, signal?: AbortSignal) => request<RunTrace>(`/analysis-runs/${encodeURIComponent(id)}/trace`, { signal }),
  answer: (id: string, questionId: string, answer: string, key: string, signal?: AbortSignal) => request<DialogueResult>(`/insights/${encodeURIComponent(id)}/questions`, {
    method: 'POST', signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ question_id: questionId, answer, idempotency_key: key }),
  }),
  message: (id: string, text: string, key: string, signal?: AbortSignal) => request<DialogueResult>(`/insights/${encodeURIComponent(id)}/messages`, {
    method: 'POST', signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text, idempotency_key: key }),
  }),
};
