export interface Actor {
  id: string;
  email: string;
  name: string;
  role: string;
  groups: string[];
  scopes?: string[] | null;
  resource_ids?: string[] | null;
  active?: boolean;
}

export interface Resource {
  id: string;
  kind: string;
  name: string;
  parent_id: string | null;
  owner_id: string;
  visibility: 'private' | 'company';
  acl: Record<string, string>;
  data: Record<string, any>;
  version: number;
  created_at: string;
  updated_at: string;
  deleted_at?: string | null;
}

export interface NewResource {
  kind: string;
  name: string;
  data: Record<string, any>;
  parent_id?: string | null;
  visibility?: string;
  acl?: Record<string, string>;
}

export class ApiError extends Error {
  status: number;
  code: string;
  constructor(message: string, status = 500, code = 'request_failed') {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
  }
}

export function apiUrl(path: string) {
  return path.startsWith('/api/') ? path : `/api/v1${path.startsWith('/') ? path : `/${path}`}`;
}

export async function api<T = any>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers);
  headers.set('Accept', 'application/json');
  if (options.method && options.method.toUpperCase() !== 'GET') {
    headers.set('X-Blueprint-Request', '1');
    if (!(options.body instanceof FormData)) headers.set('Content-Type', 'application/json');
  }
  const response = await fetch(apiUrl(path), { ...options, headers, credentials: 'same-origin' });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new ApiError(body?.error?.message ?? body?.message ?? `請求失敗（${response.status}）`, response.status, body?.error?.code);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export const listResources = (kind: string, q?: string) => api<Resource[]>(`/resources/${kind}${q ? `?q=${encodeURIComponent(q)}` : ''}`);
export const getResources = listResources;
export const getResource = (id: string) => api<Resource>(`/resource/${id}`);
export const createResource = (input: NewResource) => api<Resource>('/resources', { method: 'POST', body: JSON.stringify(input) });
export const updateResource = (resource: Resource, changes: Partial<NewResource>) => api<Resource>(`/resource/${resource.id}`, {
  method: 'PATCH', body: JSON.stringify({ ...changes, expected_version: resource.version }),
});
export const deleteResource = (resource: Resource) => api(`/resource/${resource.id}?expected_version=${resource.version}`, { method: 'DELETE' });

export function canEditResource(resource: Resource, actor: Actor) {
  if (actor.role === 'viewer' && !(resource.kind === 'collection' && resource.owner_id === actor.id && resource.visibility === 'private')) return false;
  if (['owner', 'admin'].includes(actor.role) || resource.owner_id === actor.id) return true;
  const roles = [resource.acl?.[`user:${actor.id}`], ...(actor.groups ?? []).map(id => resource.acl?.[`group:${id}`])];
  return roles.includes('editor') || resource.kind === 'folder' && roles.includes('uploader');
}

export function canManageResource(resource: Resource, actor: Actor) {
  return ['owner', 'admin'].includes(actor.role) || resource.owner_id === actor.id;
}

export function extractList<T = any>(value: any): T[] {
  if (Array.isArray(value)) return value;
  return value?.items ?? value?.resources ?? value?.users ?? value?.tokens ?? value?.jobs ?? value?.events ?? value?.data ?? [];
}

export function formatDate(value?: string | null, dateOnly = false) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString('zh-HK', dateOnly
    ? { month: 'short', day: 'numeric' }
    : { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

export function formatBytes(bytes: number = 0) {
  if (!Number.isFinite(bytes) || bytes < 0) return '—';
  if (!bytes) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const unit = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return `${(bytes / 1024 ** unit).toFixed(unit > 0 ? 1 : 0)} ${units[unit]}`;
}

export async function downloadFile(path: string, name?: string, headers?: HeadersInit) {
  const response = await fetch(apiUrl(path), { credentials: 'same-origin', headers });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new ApiError(body?.error?.message ?? `下載失敗（${response.status}）`, response.status);
  }
  const objectUrl = URL.createObjectURL(await response.blob());
  const anchor = document.createElement('a');
  anchor.href = objectUrl;
  anchor.download = name ?? 'download';
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(objectUrl), 10_000);
}

export function safeExternalUrl(value: unknown): string | null {
  if (typeof value !== 'string') return null;
  try { const url = new URL(value); return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password ? url.href : null; }
  catch { return null; }
}
