import type { Env } from './types';
import { HttpError } from './http';
// Keep authorization reads on D1's primary. No replica sessions or positive grant cache.
export const ACTIVE_GRANT = `EXISTS (SELECT 1 FROM repo_grants g JOIN machines m ON m.id=g.machine_id
  WHERE g.machine_id=? AND g.repo_id=? AND m.revoked_at IS NULL)`;
export async function tokenHash(token: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(token));
  return [...new Uint8Array(digest)].map(n => n.toString(16).padStart(2, '0')).join('');
}
export async function authenticate(request: Request, env: Env): Promise<string> {
  const match = /^Bearer ([A-Za-z0-9_-]{43,128})$/.exec(request.headers.get('Authorization') ?? '');
  if (!match) throw new HttpError(401, 'Invalid machine credential');
  const row = await env.DB.prepare('SELECT id FROM machines WHERE token_hash=? AND revoked_at IS NULL')
    .bind(await tokenHash(match[1])).first<{id: string}>();
  if (!row) throw new HttpError(401, 'Invalid machine credential');
  return row.id;
}
export async function grant(env: Env, machine: string, repo: string): Promise<void> {
  const row = await env.DB.prepare(`SELECT 1 AS allowed WHERE ${ACTIVE_GRANT}`)
    .bind(machine, repo).first();
  if (!row) throw new HttpError(403, 'Repository not allowed');
}
