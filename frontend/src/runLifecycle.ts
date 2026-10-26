import type { AnalysisRun } from './api';

export type Attempt = { key: string; status: 'pending' | 'unknown' };
export function isRunDisabled(hasCompany: boolean, busy: boolean, attempt?: Attempt): boolean {
  return !hasCompany || busy || attempt?.status === 'pending' || attempt?.status === 'unknown';
}
export type RunApi = {
  run: (company: string, role: 'CFO' | 'COO', signal: AbortSignal, key: ReturnType<typeof crypto.randomUUID>) => Promise<AnalysisRun>;
  getRun: (id: string, signal: AbortSignal) => Promise<AnalysisRun>;
};

export type RunOutcome = { status: 'succeeded' | 'failed' | 'unknown'; execution_mode?: AnalysisRun['execution_mode'] };

export function runSuccessMessage(mode: AnalysisRun['execution_mode']): string {
  if (mode === 'local_ollama') return 'Анализ локальной LLM сервера · экспериментальный режим.';
  return mode === 'agentic_fixture' ? 'Анализ на тестовой модели.' : mode === 'mvp_offline' ? 'Локальный анализ · тестовая модель, без живой LLM.' : mode === 'broker_live' ? 'Анализ с разрешённой живой моделью.' : 'Демонстрационный анализ · синтетические данные.';
}

export async function executeRun(api: RunApi, company: string, role: 'CFO' | 'COO', key: ReturnType<typeof crypto.randomUUID>,
  signal: AbortSignal, onAttempt: (attempt: Attempt | null) => void,
  wait: (signal: AbortSignal) => Promise<void>): Promise<RunOutcome> {
  onAttempt({ key, status: 'pending' });
  try {
    let run = await api.run(company, role, signal, key);
    validateRun(run);
    while (run.status === 'queued' || run.status === 'running') {
      await wait(signal);
      run = await api.getRun(run.run_id, signal);
      validateRun(run);
    }
    if (run.status === 'unknown_outcome') {
      onAttempt({ key, status: 'unknown' });
      return { status: 'unknown' };
    }
    onAttempt(null);
    return { status: run.status, execution_mode: run.execution_mode };
  } catch {
    // Any failure after POST starts leaves the outcome uncertain, including abort and GET errors.
    onAttempt({ key, status: 'unknown' });
    return { status: 'unknown' };
  }
}

function validateRun(run: AnalysisRun): void {
  if (!run || !['queued', 'running', 'succeeded', 'failed', 'unknown_outcome'].includes(run.status)
    || ((run.status === 'queued' || run.status === 'running') && !run.run_id)) throw new Error('Invalid run response');
}

export function waitForPoll(signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) { reject(new DOMException('Aborted', 'AbortError')); return; }
    const timer = window.setTimeout(() => { signal.removeEventListener('abort', abort); resolve(); }, 900);
    const abort = () => { clearTimeout(timer); reject(new DOMException('Aborted', 'AbortError')); };
    signal.addEventListener('abort', abort, { once: true });
  });
}
