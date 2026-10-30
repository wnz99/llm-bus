/** Per-machine authenticated, same-repository durable relay. */
import type { Env } from './types';
import { HttpError, text, UUID } from './http';
import { authenticate } from './auth';
import { register, sessions } from './sessions';
import { send, inbox, receipt, status, dispatch, repair } from './messages';
import { readReport, submitReport, batchRead } from './reports';

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    try {
      const machine = await authenticate(request, env);
      const url = new URL(request.url);
      const route = `${request.method} ${url.pathname}`;
      switch (route) {
        case 'GET /v1/capabilities': return Response.json({reply_to:true,batch_status:true,batch_reports:true});
        case 'POST /v1/sessions': return await register(request, env, machine);
        case 'GET /v1/sessions': return await sessions(url, env, machine);
        case 'POST /v1/messages': return await send(request, env, machine);
        case 'GET /v1/inbox': return await inbox(url, env, machine);
        case 'POST /v1/receipts': return await receipt(request, env, machine, false);
        case 'POST /v1/ack': return await receipt(request, env, machine, true);
        case 'POST /v1/reports': return await submitReport(request, env, machine);
        case 'GET /v1/reports': return await readReport(url, env, machine);
        case 'GET /v1/status': return await status(url, env, machine);
        case 'POST /v1/status/batch': return await batchRead(request, env, machine, 'status');
        case 'POST /v1/reports/batch': return await batchRead(request, env, machine, 'reports');
        default: throw new HttpError(404, 'Unknown endpoint');
      }
    } catch (error) {
      if (error instanceof HttpError) return Response.json({error: error.message}, {status: error.status});
      // Do not emit request headers, message bodies, tokens, or SQL errors.
      return Response.json({error: 'Relay temporarily unavailable'}, {status: 503});
    }
  },
  async queue(batch: MessageBatch<string>, env: Env): Promise<void> {
    for (const message of batch.messages) {
      try { text(message.body, UUID); await dispatch(env, message.body); message.ack(); }
      catch { message.retry(); }
    }
  },
  async scheduled(_event: ScheduledController, env: Env): Promise<void> { await repair(env); }
};
