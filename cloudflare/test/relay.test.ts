import { describe, expect, it } from 'vitest';
import worker from '../src/index';
import { dispatch, repair } from '../src/messages';
import { bindings, request, register, send } from './relay-fixture';
describe('machine and repository boundary', () => {
  it('compares the reply UUID again after a raced duplicate insert', async () => {
    const {message} = await send();
    const reply = {id:crypto.randomUUID(),sender:'codex:b',recipient:'a/xyz/codex:a',repo_id:'xyz',body:'reply',reply_to:message.id};
    expect((await request('b','messages',reply)).status).toBe(202);
    const original=bindings.DB;
    for (const linked of [true,false]) {
      let hidePrior=true;
      bindings.DB=new Proxy(original,{get(target,key) {
        if (key!=='prepare') return Reflect.get(target,key,target);
        return (sql:string) => {
          const statement=target.prepare(sql);
          if (sql!=='SELECT * FROM messages WHERE id=?') return statement;
          return new Proxy(statement,{get(inner,key) {
            if (key!=='bind') return Reflect.get(inner,key,inner);
            return (...args:unknown[]) => {
              const bound=inner.bind(...args);
              return new Proxy(bound,{get(query,key) {
                if (key!=='first') return Reflect.get(query,key,query);
                return async () => {
                  if (hidePrior) {hidePrior=false; return null;}
                  return query.first();
                };
              }});
            };
          }});
        };
      }});
      try {
        const result=await request('b','messages',{...reply,reply_to:linked ? message.id : undefined});
        expect(result.status).toBe(linked ? 200 : 409);
      } finally {bindings.DB=original;}
    }
  });
  it('advertises reply support and binds links to participants, repository, and immutable UUID', async () => {
    expect(await (await request('a','capabilities')).json()).toEqual({reply_to:true,batch_status:true,batch_reports:true});
    const {message} = await send();
    const reply = {id:crypto.randomUUID(),sender:'codex:b',recipient:'a/xyz/codex:a',repo_id:'xyz',body:'reply',reply_to:message.id};
    expect((await request('b','messages',reply)).status).toBe(202);
    expect((await request('b','messages',reply)).status).toBe(200);
    expect((await request('b','messages',{...reply,reply_to:undefined})).status).toBe(409);
    await register('b','xyz','codex:other');
    expect((await request('b','messages',{...reply,id:crypto.randomUUID(),sender:'codex:other'})).status).toBe(403);
    expect((await request('b','messages',{...reply,id:crypto.randomUUID(),reply_to:crypto.randomUUID()})).status).toBe(403);
    expect((await request('b','messages',{...reply,id:crypto.randomUUID(),reply_to:1})).status).toBe(400);
    expect((await request('b','messages',{...reply,id:crypto.randomUUID(),reply_to:null})).status).toBe(400);
    await dispatch(bindings,reply.id);
    expect(await (await request('a','inbox?repo_id=xyz')).json()).toMatchObject([{reply_to:message.id}]);
    await bindings.DB.prepare("DELETE FROM repo_grants WHERE machine_id='a'").run();
    expect((await request('b','messages',{...reply,id:crypto.randomUUID()})).status).toBe(403);
  });
  it('batch reads preserve item authorization and reject malformed requests', async () => {
    const {message} = await send();
    const denied = crypto.randomUUID();
    expect((await request('b', 'messages', {id:denied, sender:'codex:b', recipient:'a/xyz/codex:a', repo_id:'xyz', body:'private neighbor'})).status).toBe(202);
    for (const [route, field] of [['status/batch', 'ids'], ['reports/batch', 'message_ids']]) {
      const result = await request('a', route, {[field]: [message.id, denied]});
      expect(result.status).toBe(200);
      expect(await result.json()).toMatchObject({results: [
        {id: message.id, error: null, value: route.startsWith('status') ? {id:message.id} : {message_id:message.id}},
        {id: denied, value: null, error: {code:'not_allowed'}},
      ]});
      for (const data of [{[field]:[]}, {[field]:[message.id,message.id]}, {[field]:['invalid']}, {[field]:[message.id], extra:1}, {[field]:Array.from({length:101},()=>crypto.randomUUID())}]) {
        expect((await request('a',route,data)).status).toBe(400);
      }
    }
    await bindings.DB.prepare("DELETE FROM repo_grants WHERE machine_id='a'").run();
    for (const [route, field] of [['status/batch', 'ids'], ['reports/batch', 'message_ids']]) {
      expect(await (await request('a',route,{[field]:[message.id]})).json()).toEqual({results:[{id:message.id,value:null,error:{code:'not_allowed'}}]});
    }
  });
  it('rejects invalid credentials and unauthorized registration/discovery', async () => {
    const invalid = await worker.fetch(new Request('https://bus.test/v1/sessions?repo_id=xyz'), bindings);
    expect(invalid.status).toBe(401);
    expect((await register('c')).status).toBe(403);
    expect((await request('c', 'sessions?repo_id=xyz')).status).toBe(403);
    expect((await request('a', 'sessions?repo_id=xyz')).status).toBe(200);
    expect((await request('a', 'inbox?repo_id=other')).status).toBe(403);
  });
  it('rejects cross-repo recipients and spoofed sender sessions', async () => {
    await register('c', 'other');
    const {message} = await send();
    expect((await request('a', 'messages', {...message, id: crypto.randomUUID(), recipient: 'c/other/codex:c'})).status).toBe(403);
    expect((await request('a', 'messages', {...message, id: crypto.randomUUID(), sender: 'codex:b'})).status).toBe(403);
    const count = await bindings.DB.prepare('SELECT count(*) AS n FROM messages').first<{n:number}>();
    expect(count?.n).toBe(1);
  });
  it('revokes queued delivery and acknowledgements when a grant or machine is revoked', async () => {
    const {message} = await send(); await dispatch(bindings, message.id);
    expect((await request('a', 'receipts', {id: message.id})).status).toBe(403);
    await bindings.DB.prepare("DELETE FROM repo_grants WHERE machine_id='a'").run();
    expect(await (await request('b', 'inbox?repo_id=xyz')).json()).toEqual([]);
    expect((await request('b', 'receipts', {id: message.id})).status).toBe(403);
    await bindings.DB.prepare("UPDATE machines SET revoked_at='now' WHERE id='b'").run();
    expect((await request('b', 'inbox?repo_id=xyz')).status).toBe(401);
  });
});
describe('durability and receipts', () => {
  it('deduplicates identical uploads, rejects conflicting UUID reuse, and keeps reads pending', async () => {
    const {message, response} = await send(); expect(response.status).toBe(202);
    expect((await request('a', 'messages', message)).status).toBe(200);
    expect((await request('a', 'messages', {...message, body: 'different'})).status).toBe(409);
    await dispatch(bindings, message.id); await dispatch(bindings, message.id);
    for (let n=0; n<2; n++) {
      const inbox = await (await request('b', 'inbox?repo_id=xyz')).json() as unknown[];
      expect(inbox).toHaveLength(1);
    }
    expect((await request('b', 'ack', {id: message.id})).status).toBe(403);
    expect((await request('b', 'receipts', {id: message.id})).status).toBe(200);
    expect((await request('b', 'receipts', {id: message.id})).status).toBe(200);
    expect(await (await request('b', 'inbox?repo_id=xyz')).json()).toEqual([]);
    expect((await request('b', 'ack', {id: message.id})).status).toBe(200);
    expect(await (await request('a', `status?id=${message.id}`)).json()).toMatchObject({id: message.id, status: 'acknowledged', report_expires_at: expect.any(String)});
  });
  it('finds earlier messages when queue delivery is reversed and a new scan starts', async () => {
    const first = await send(); const second = await send();
    await dispatch(bindings, second.message.id);
    const later = await (await request('b', 'inbox?repo_id=xyz&after=0')).json() as {id:string}[];
    expect(later.map(m=>m.id)).toEqual([second.message.id]);
    await request('b', 'receipts', {id: second.message.id});
    await dispatch(bindings, first.message.id);
    const earlier = await (await request('b', 'inbox?repo_id=xyz&after=0')).json() as {id:string}[];
    expect(earlier.map(m=>m.id)).toEqual([first.message.id]);
  });
  it('retains dispatch records for scheduled repair and reports expiry', async () => {
    const {message} = await send();
    await repair(bindings);
    expect(await bindings.DB.prepare('SELECT id FROM messages WHERE ready=0').first()).not.toBeNull();
    await bindings.DB.prepare("UPDATE messages SET sent_at='2020-01-01T00:00:00.000Z' WHERE id=?").bind(message.id).run();
    expect(await (await request('a', `status?id=${message.id}`)).json()).toEqual({id: message.id, status: 'expired', report_expires_at: '2020-01-31T00:00:00.000Z'});
    expect(await (await request('b', 'inbox?repo_id=xyz')).json()).toEqual([]);
  });
});

it('accepts valid escaped payloads and durable sends after sessions end', async () => {
  await bindings.DB.prepare('UPDATE sessions SET ended=1').run();
  const message = {id:crypto.randomUUID(), sender:'codex:a',recipient:'b/xyz/codex:b',repo_id:'xyz',body:'\u0001'.repeat(65536)};
  expect((await request('a','messages',message)).status).toBe(202);
  const unicode = {...message,id:crypto.randomUUID(),body:'界'.repeat(16000)};
  expect((await request('a','messages',unicode)).status).toBe(202);
  expect((await request('a','messages',{...message,id:crypto.randomUUID(),body:'界'.repeat(22000)})).status).toBe(400);
});
