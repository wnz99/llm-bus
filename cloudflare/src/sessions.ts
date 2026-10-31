import type { Env, Session } from './types';
import { HttpError, jsonBody, text, ID, NATIVE } from './http';
import { ACTIVE_GRANT, grant } from './auth';
export function qualified(machine: string, repo: string, native: string): string {
  return `${machine}/${repo}/${native}`;
}
export async function register(request: Request, env: Env, machine: string): Promise<Response> {
  const data = await jsonBody(request);
  const repo = text(data.repo_id, ID);
  const native = text(data.native_address, NATIVE);
  const seen = text(data.last_seen);
  const timestamp = Date.parse(seen);
  if (!Number.isFinite(timestamp) || timestamp > Date.now() + 60_000 || typeof data.ended !== 'boolean') {
    throw new HttpError(400, 'Invalid session activity');
  }
  const address = qualified(machine, repo, native);
  const result = await env.DB.prepare(`INSERT INTO sessions
    (address,machine_id,repo_id,native_address,last_seen,ended)
    SELECT ?,?,?,?,?,? WHERE ${ACTIVE_GRANT}
    ON CONFLICT(address) DO UPDATE SET last_seen=excluded.last_seen, ended=excluded.ended
    WHERE excluded.last_seen >= sessions.last_seen`)
    .bind(address, machine, repo, native, new Date(timestamp).toISOString(), Number(data.ended), machine, repo).run();
  // Stale updates are harmless, but a missing grant is always an error.
  if (!result.meta.changes) await grant(env, machine, repo);
  return Response.json({address});
}
export async function sessions(url: URL, env: Env, machine: string): Promise<Response> {
  const repo = text(url.searchParams.get('repo_id'), ID);
  await grant(env, machine, repo);
  const rows = await env.DB.prepare(`SELECT s.* FROM sessions s JOIN machines d ON d.id=s.machine_id
    JOIN repo_grants g ON g.machine_id=s.machine_id AND g.repo_id=s.repo_id
    WHERE s.repo_id=? AND s.machine_id<>? AND s.ended=0 AND d.revoked_at IS NULL
    AND s.last_seen > strftime('%Y-%m-%dT%H:%M:%fZ','now','-24 hours')
    AND ${ACTIVE_GRANT} ORDER BY s.address LIMIT 1000`)
    .bind(repo, machine, machine, repo).all<Session>();
  return Response.json(rows.results);
}
