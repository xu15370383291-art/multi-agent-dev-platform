export class ApiError extends Error { constructor(message:string, readonly status:number) { super(message) } }
export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch('/api/v1' + path, {
    ...init, headers: { 'Content-Type': 'application/json', ...init?.headers },
  })
  const body = await response.json().catch(() => ({ detail: '服务返回了无法解析的响应' }))
  if (!response.ok) throw new ApiError(typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail), response.status)
  return body as T
}

export type Adapter = { id: string; label: string; version: string; supports_thinking: boolean; supports_thinking_budget?: boolean }
export type Profile = { id: string; revision_id?: string; name: string; revision: number; base_url: string; model_id: string;
  max_output_tokens?: number; timeout_seconds?: number; thinking_budget?: number | null;
  adapter?: Adapter; capabilities_stale?: boolean;
  thinking_mode: 'provider_default' | 'enabled' | 'disabled';
  protocol: string; capabilities: Record<string, { status: string; error_type?: string; error_code?: string; http_status?: number; message?: string; reasoning_observed?: boolean }> }
export type Project = { id: string; name: string; project_type: string; requirement: string }
export type Run = { id: string; status: string; phase: string; reason: string | null; model_calls: number; max_model_calls: number | null; call_policy?: 'fixed'|'progress_guarded'; max_seconds?:number|null;created_at?:string;recovery_open?:boolean }
export type Doc = { id: string; run_id: string; kind: string; version: number; markdown: string; content_hash: string;
  payload?: { tasks?: { id: string; title: string; depends_on?: string[] }[] } }
export type Approval = { id: string; document_id: string; run_id: string }
export type Event = { id: string; kind: string; data: Record<string, unknown>; created_at: string; run_id?: string }
export type Agent = { role: string; purpose: string; connected: boolean }
export type Binding = { scope: string; role: string; profile_id: string }
export type Candidate = { id: string; commit_sha: string; status: string; lock_hash: string | null;
  build_result: { strategy?: string; error_code?: string; stdout?: string; stderr?: string } }
export type QualityReport = { id: string; candidate_id: string; commit_sha: string; role: string; status: string; attempt?: number; supersedes_id?: string;
  asset_hash?: string; evidence_hash?: string; payload: Record<string, unknown> }
export type Delivery = { id: string; candidate_id: string; archive_hash: string;
  manifest: { commit_sha: string; preview_url: string; known_issues: unknown[]; acceptance: { criterion_id: string; status: string; name: string }[] } }
