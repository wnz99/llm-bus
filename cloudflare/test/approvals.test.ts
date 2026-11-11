import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import worker from '../src/index';
import { dispatch } from '../src/messages';
import { bindings, request } from './relay-fixture';

const token = (machine: string) => machine.repeat(43);
const call = (machine: string, path: string, data?: object) => worker.fetch(new Request(`https://bus.test/v2/coordination/${path}`, {
  method: data ? 'POST' : 'GET', headers: {Authorization: `Bearer ${token(machine)}`, 'Content-Type': 'application/json'}, body: data ? JSON.stringify(data) : undefined,
}), bindings);
const sha = 'a'.repeat(40);
const direction = (operation: string) => ({version: 1, project_id: crypto.randomUUID(), generation: crypto.randomUUID(), grant_id: crypto.randomUUID(), operation});
const approvals: Record<string, Record<string, unknown>> = {
  'approve-commit': {ticket: 'T-1', branch: 'feat/x', base: 'develop', expires_at: '2030-01-01T00:00:00Z'},
  'approve-push': {ticket: 'T-1', branch: 'feat/x', base: 'develop', expires_at: '2030-01-01T00:00:00+02:00', head_sha: sha},
  'approve-merge': {ticket: 'T-1', branch: 'feat/x', base: 'develop', expires_at: '2030-01-01T00:00:00.5Z', head_sha: sha, pr: 7},
  'approve-merge-admin': {ticket: 'T-1', branch: 'feat/x', base: 'develop', expires_at: '2030-01-01T00:00:00Z', head_sha: 'b'.repeat(64), pr: 7},
};
const envelope = (operation: string, approval?: unknown) => ({
  id: crypto.randomUUID(), sender: 'codex:a', recipient: 'b/xyz/codex:b', repo_id: 'xyz', body: 'approval',
  coordination: direction(operation), ...(approval === undefined ? {} : {approval}),
});

describe('approval transport', () => {
  it('keeps the existing capability responses and adds the approval capability', async () => {
    expect(await (await call('a', 'capabilities')).json()).toEqual({version: 1});
    expect(await (await call('a', 'approvals/capabilities')).json()).toEqual({approval_version: 1});
  });
  it('migrates the prior schema without changing history', async () => {
    await bindings.DB.prepare('ALTER TABLE messages DROP COLUMN approval').run();
    const id = crypto.randomUUID();
    await bindings.DB.prepare('INSERT INTO messages(id,sender,recipient,repo_id,body) VALUES (?,?,?,?,?)').bind(id, 'a/xyz/codex:a', 'b/xyz/codex:b', 'xyz', 'retained').run();
    await bindings.DB.prepare(readFileSync(new URL('../migrations/0007_coordination_approvals.sql', import.meta.url), 'utf8')).run();
    expect(await bindings.DB.prepare('SELECT body,approval FROM messages WHERE id=?').bind(id).first()).toEqual({body: 'retained', approval: null});
  });
  it('accepts each approval operation, replays it and delivers the approval on the inbox', async () => {
    for (const [operation, approval] of Object.entries(approvals)) {
      const message = envelope(operation, approval);
      expect((await call('a', 'messages', message)).status).toBe(202);
      expect((await call('a', 'messages', structuredClone(message))).status).toBe(200);
      await dispatch(bindings, message.id);
    }
    const page = await (await call('b', 'inbox?repo_id=xyz')).json() as Record<string, unknown>[];
    expect(page.map(row => (row.coordination as {operation: string}).operation)).toEqual(Object.keys(approvals));
    expect(page.map(row => row.approval)).toEqual(Object.values(approvals));
  });
  it('compares the approval on idempotent replay', async () => {
    const message = envelope('approve-merge', approvals['approve-merge']);
    expect((await call('a', 'messages', message)).status).toBe(202);
    for (const change of [{pr: 8}, {base: 'main'}, {head_sha: 'c'.repeat(40)}, {ticket: 'T-2'}, {expires_at: '2031-01-01T00:00:00Z'}]) {
      expect((await call('a', 'messages', {...message, approval: {...approvals['approve-merge'], ...change}})).status).toBe(409);
    }
    expect((await call('a', 'messages', {...message, approval: {...approvals['approve-merge'], branch: 'feat/x'}})).status).toBe(200);
  });
  it('requires an approval exactly for approval operations', async () => {
    for (const operation of Object.keys(approvals)) expect((await call('a', 'messages', envelope(operation))).status).toBe(400);
    for (const operation of ['assign', 'reprioritize', 'clarify', 'pause', 'cancel', 'review']) {
      expect((await call('a', 'messages', envelope(operation, approvals['approve-push']))).status).toBe(400);
      expect((await call('a', 'messages', envelope(operation))).status).toBe(202);
    }
  });
  it('keeps approvals off the ordinary and availability classes', async () => {
    const ordinary = {id: crypto.randomUUID(), sender: 'codex:a', recipient: 'b/xyz/codex:b', repo_id: 'xyz', body: 'x', approval: approvals['approve-push']};
    expect((await request('a', 'messages', ordinary)).status).toBe(400);
    expect((await call('a', 'availability/messages', {...ordinary, availability: {version: 1, project_id: crypto.randomUUID(), generation: crypto.randomUUID(), grant_id: crypto.randomUUID(), kind: 'joined'}})).status).toBe(400);
    expect((await request('a', 'messages', {...ordinary, approval: undefined, coordination: direction('approve-push')})).status).toBe(400);
  });
  it('rejects invalid approval fields, extra keys and record_id', async () => {
    const base = approvals['approve-merge'];
    const bad: [string, Record<string, unknown>][] = [
      ['approve-merge', {...base, record_id: 'a'.repeat(32)}],
      ['approve-commit', {...approvals['approve-commit'], record_id: 'a'.repeat(32)}],
      ['approve-merge', {...base, extra: 1}],
      ['approve-merge', {ticket: 'T-1', branch: 'feat/x', base: 'develop', head_sha: sha, pr: 7}],
      ['approve-merge', {...base, ticket: ''}],
      ['approve-merge', {...base, ticket: 'x'.repeat(129)}],
      ['approve-merge', {...base, ticket: 'a\nb'}],
      ['approve-merge', {...base, ticket: 1}],
      ['approve-merge', {...base, branch: '-x'}],
      ['approve-merge', {...base, branch: 'a..b'}],
      ['approve-merge', {...base, base: 'a b'}],
      ['approve-merge', {...base, expires_at: '2030-01-01'}],
      ['approve-merge', {...base, expires_at: '2030-02-30T00:00:00Z'}],
      ['approve-merge', {...base, expires_at: '2030-01-01T24:00:00Z'}],
      ['approve-merge', {...base, expires_at: '2030-01-01T00:00:00'}],
      ['approve-merge', {...base, pr: 0}],
      ['approve-merge', {...base, pr: true}],
      ['approve-merge', {...base, pr: '7'}],
      ['approve-merge', {...base, pr: undefined}],
      ['approve-push', {...approvals['approve-push'], pr: 7}],
      ['approve-merge', {...base, head_sha: undefined}],
      ['approve-push', {...approvals['approve-push'], head_sha: undefined}],
      ['approve-merge', {...base, head_sha: 'A'.repeat(40)}],
      ['approve-merge', {...base, head_sha: 'a'.repeat(41)}],
      ['approve-commit', {...approvals['approve-commit'], head_sha: sha}],
    ];
    for (const [operation, approval] of bad) expect((await call('a', 'messages', envelope(operation, approval))).status, JSON.stringify([operation, approval])).toBe(400);
    for (const approval of [null, [], 'x', 1]) expect((await call('a', 'messages', envelope('approve-push', approval))).status).toBe(400);
  });
  it('leaves messages without an approval unchanged on the wire', async () => {
    const message = envelope('assign');
    expect((await call('a', 'messages', message)).status).toBe(202);
    await dispatch(bindings, message.id);
    const page = await (await call('b', 'inbox?repo_id=xyz')).json() as Record<string, unknown>[];
    expect(page[0]).toMatchObject({id: message.id, coordination: message.coordination});
    expect(page[0]).not.toHaveProperty('approval');
  });
});
