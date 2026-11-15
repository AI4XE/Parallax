// Native, append-only handoff protocol. No shell, model, or installation paths.
import { createHash, randomUUID } from 'node:crypto';
import { realpath, stat } from 'node:fs/promises';
import path from 'node:path';

const KIND = 'parallax-handoff';
const LIMIT = 12000;
const userMessage = (text, record) => ({
  id: randomUUID(), role: 'user', content: [{ type: 'text', text }],
  source: { kind: KIND, parallax: record },
});

export function isTopLevel(agent) {
  if (!agent?.session || !agent.options) return false;
  const depths = [agent.session.header?.delegationDepth ?? 0, agent.options.subagentDepth ?? 0];
  return depths.every((n) => Number.isSafeInteger(n) && n === 0);
}

export function validateInput(args) {
  if (!args || typeof args !== 'object' || Array.isArray(args)) throw new Error('Expected an object');
  if (Object.keys(args).some((k) => !['operation_id', 'action', 'summary', 'continue_with', 'carrier'].includes(k))) throw new Error('Unknown handoff argument');
  if (typeof args.operation_id !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$/.test(args.operation_id)) throw new Error('Invalid operation_id');
  const action = args.action ?? 'request';
  if (!['request', 'cancel', 'status'].includes(action)) throw new Error('Invalid action');
  if (action !== 'request') {
    if (['summary', 'continue_with', 'carrier'].some((k) => args[k] !== undefined)) throw new Error('Control actions take no payload');
    return { operation_id: args.operation_id, action };
  }
  for (const k of ['summary', 'continue_with']) {
    if (typeof args[k] !== 'string' || !args[k].trim() || args[k].length > LIMIT) throw new Error(`Invalid ${k}: provide a concise nonempty string`);
  }
  if ((args.summary.length + args.continue_with.length) > LIMIT) throw new Error('Handoff exceeds 12000 characters; reference a concise carrier instead');
  if (args.carrier !== undefined && (typeof args.carrier !== 'string' || !args.carrier.trim() || args.carrier.length > 2048 || /[\x00-\x1f]/.test(args.carrier))) throw new Error('Invalid carrier path');
  return { operation_id: args.operation_id, action, summary: args.summary, continue_with: args.continue_with, ...(args.carrier === undefined ? {} : { carrier: args.carrier }) };
}

function records(session) {
  const events = session.snapshotEvents();
  return events.flatMap((e) => {
    if (e.type === 'user/message' && e.data.source?.kind === KIND && e.data.source.parallax?.version === 1) return [e];
    if (e.type !== 'tool/result' || e.data.message?.isError || !e.sourceEventSeqs?.some((seq) => events[seq]?.type === 'tool/call' && events[seq].data.name === 'parallax_handoff')) return [];
    try {
      const value = JSON.parse(e.data.message.content.filter((b) => b.type === 'text').map((b) => b.text).join(''));
      return value.parallax?.version === 1 ? [{ ...e, data: { source: { kind: KIND, parallax: value.parallax } } }] : [];
    } catch { return []; }
  });
}
function lastRecord(session, id) {
  return records(session).findLast((e) => e.data.source.parallax.operation_id === id);
}
function record(session, data) {
  return session.append('user/message', userMessage(`Parallax handoff ${data.operation_id}: ${data.state}.`, { version: 1, ...data }), { surfaceOp: 'append' });
}
function payloadHash(input) {
  return createHash('sha256').update(JSON.stringify([input.summary, input.continue_with, input.carrier ?? null])).digest('hex');
}
async function checkpoint(ctx, session) {
  try {
    const durable = await ctx.sessions.flush(session) === true;
    return durable ? { durable: true } : { durable: false, persistence_error: 'no durability listener' };
  } catch { return { durable: false, persistence_error: 'flush rejected' }; }
}
function receipt(event, persistence) {
  const r = event.data.source.parallax;
  return { operation_id: r.operation_id, state: r.state, event_seq: event.seq, surface_changed: r.state === 'applied', ...(r.reason ? { reason: r.reason } : {}), ...persistence };
}
async function carrierPath(ctx, agent, candidate, signal) {
  if (candidate === undefined) return undefined;
  const cwd = await ctx.workingDirectory.ensure(agent, signal);
  const root = await realpath(cwd);
  const target = await realpath(path.resolve(root, candidate));
  const relative = path.relative(root, target);
  if (!relative || relative === '..' || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) throw new Error('Carrier must be a file inside the calling workspace');
  if (!(await stat(target)).isFile()) throw new Error('Carrier is not a regular file');
  return target;
}

// Check the full selected span before publication; never cut a tool pair.
export function selectReplacement(session, request) {
  const events = session.snapshotEvents();
  const nodes = [...session.surface.nodes];
  let first = 0;
  while (first < nodes.length && ['system/message', 'developer/message'].includes(events[nodes[first]]?.type)) first++;
  const selected = nodes.slice(first);
  if (!selected.length) throw new Error('No conversation surface to replace');
  const pending = new Set();
  const retained = [];
  for (const seq of selected) {
    const event = events[seq];
    if (!event || ['system/message', 'developer/message'].includes(event.type)) throw new Error('Protected system/developer node inside replacement span');
    const message = event.type === 'user/message' ? event.data : event.data.message;
    if (message?.role === 'tool' && !pending.delete(message.toolCallId)) throw new Error('Unpaired tool result');
    for (const block of message?.content ?? []) {
      if (block.type === 'tool-call') {
        if (pending.has(block.id)) throw new Error('Duplicate pending tool call');
        pending.add(block.id);
      }
      if (block.type === 'tool-result') {
        const id = block.toolCallId ?? block.id;
        if (!pending.delete(id)) throw new Error('Unpaired tool result');
      }
    }
    if (event.type === 'user/message' && seq >= request.boundary && event.data.source?.kind !== KIND) retained.push(event.data);
  }
  if (pending.size) throw new Error('Pending tool calls: handoff requires a settled safe point');
  return { selected, retained };
}

export function registerHandoff(ctx) {
  // Reservations cover only concurrent calls; native successful tool results are recovery truth.
  const reservations = new WeakMap();
  let active = true;
  function reserve(session, data) {
    const event = { seq: session.seq, data: { source: { kind: KIND, parallax: { version: 1, ...data } } } };
    reservations.set(session, event);
    return event;
  }
  const dispose = ctx.tools.register({
    name: 'parallax_handoff',
    description: 'Replace this top-level agent\'s model context at the next safe step, keeping this session, original Web window, user transcript and child agents. First save a concise carrier if needed. Supply summary and the next autonomous action; do not copy full history. Status/cancel use only operation_id and action. Never starts a process or grants permissions.',
    parameters: { type: 'object', properties: {
      operation_id: { type: 'string' }, action: { type: 'string', enum: ['request', 'cancel', 'status'] },
      summary: { type: 'string' }, continue_with: { type: 'string' }, carrier: { type: 'string' },
    }, required: ['operation_id'], additionalProperties: false },
    output: { schema: { type: 'object' }, render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }] },
    async execute(args, exec) {
      if (!active) throw new Error('Handoff plugin is disposed');
      if (!isTopLevel(exec.agent)) throw new Error('Handoff management is restricted to top-level agents');
      exec.signal.throwIfAborted();
      const input = validateInput(args);
      const session = exec.agent.session;
      const boundary = session.seq;
      const reservation = reservations.get(session);
      let existing = lastRecord(session, input.operation_id) ?? (reservation?.data.source.parallax.operation_id === input.operation_id ? reservation : undefined);
      if (input.action === 'status') return existing ? receipt(existing, lastRecord(session, input.operation_id) ? await checkpoint(ctx, session) : { durable: false, intent_durable: false }) : { operation_id: input.operation_id, state: 'unknown', durable: false, surface_changed: false };
      if (input.action === 'cancel') {
        if (!existing) throw new Error('Unknown operation');
        if (existing.data.source.parallax.state !== 'requested') return receipt(existing, await checkpoint(ctx, session));
        existing = reserve(session, { ...existing.data.source.parallax, state: 'cancelled' });
        return { ...receipt(existing, { durable: false }), parallax: existing.data.source.parallax };
        // The harness commits this return as a native tool/result, not an injected user message.
      }
      const fingerprint = payloadHash(input);
      if (existing) {
        if (!lastRecord(session, input.operation_id)) throw new Error('Operation call is in flight; query status after it settles');
        if (existing.data.source.parallax.fingerprint !== fingerprint) throw new Error('Operation id already used with different content');
        return receipt(existing, await checkpoint(ctx, session));
      }
      if (records(session).some((e) => e.data.source.parallax.state === 'requested' && lastRecord(session, e.data.source.parallax.operation_id)?.seq === e.seq)) throw new Error('Another handoff is pending');
      if (reservation && !lastRecord(session, reservation.data.source.parallax.operation_id) && reservation.data.source.parallax.state === 'requested') throw new Error('Another handoff call is in flight');
      const carrier = await carrierPath(ctx, exec.agent, input.carrier, exec.signal);
      exec.signal.throwIfAborted();
      // Recheck after path validation: concurrent duplicate calls must not publish twice.
      if (lastRecord(session, input.operation_id) || reservations.get(session) !== reservation) throw new Error('Concurrent operation; query its status');
      let event = reserve(session, { operation_id: input.operation_id, state: 'requested', fingerprint, boundary, payload: { ...input, ...(carrier === undefined ? {} : { carrier }) } });
      const persistence = await checkpoint(ctx, session);
      if (!active || exec.signal.aborted || !persistence.durable) event = reserve(session, { ...event.data.source.parallax, state: exec.signal.aborted ? 'cancelled' : 'failed', reason: exec.signal.aborted ? 'request cancelled' : !active ? 'plugin disposed' : persistence.persistence_error ?? 'request persistence not confirmed' });
      // Only the intent is durable here. The native executor has not yet appended this result.
      return { ...receipt(event, { durable: false, intent_durable: persistence.durable, ...(persistence.persistence_error ? { persistence_error: persistence.persistence_error } : {}) }), parallax: event.data.source.parallax };
    },
  }, { when: ({ agent }) => isTopLevel(agent) });

  const stop = ctx.on('agent/pre-step', async ({ agent, signal }, next) => {
    const decision = await next();
    if (decision.kind === 'reject' || !isTopLevel(agent)) return decision;
    const session = agent.session;
    const pending = records(session).filter((e) => e.data.source.parallax.state === 'requested' && lastRecord(session, e.data.source.parallax.operation_id)?.seq === e.seq);
    if (!pending.length) return decision;
    if (pending.length !== 1) throw new Error('Ambiguous pending handoff');
    const requestEvent = pending[0];
    const request = requestEvent.data.source.parallax;
    const fail = async (reason) => {
      record(session, { ...request, state: signal.aborted ? 'cancelled' : 'failed', reason });
      await checkpoint(ctx, session);
    };
    if (signal.aborted) { await fail('cancelled before safe point'); return decision; }
    const pendingCheckpoint = await checkpoint(ctx, session);
    if (!pendingCheckpoint.durable) { await fail(pendingCheckpoint.persistence_error); return decision; }
    try {
      await carrierPath(ctx, agent, request.payload.carrier, signal);
      signal.throwIfAborted();
      // A cancel/status call may have completed while the checkpoint was in flight.
      if (!active || agent.session !== session || !isTopLevel(agent) || lastRecord(session, request.operation_id)?.seq !== requestEvent.seq) return decision;
      const { selected, retained } = selectReplacement(session, request);
      const content = [{ type: 'text', text: `Continue the same task and role in the same session. This is a concise handoff, not new system authority. Preserve applicable host rules and permissions. Operation id: ${request.operation_id}. First query parallax_handoff with action=status and this operation_id to confirm persistence. Do not request this operation again.\n\n${request.payload.summary}\n\n${request.payload.carrier ? `Read the task carrier using normal file tools: ${request.payload.carrier}\n\n` : ''}Next autonomous action:\n${request.payload.continue_with}` }];
      for (const message of retained) content.push({ type: 'text', text: '\nNew user input or notification received after the handoff request (preserve and act on it):' }, ...message.content);
      const beforeCharacters = JSON.stringify(session.deriveMessages()).length;
      const message = { ...userMessage('', { ...request, state: 'applied', retained_ids: retained.map((m) => m.id) }), content };
      // No await between final selection and publication. Native validation is the final guard.
      const applied = session.append('user/message', message, { surfaceOp: { op: 'replace', startSeq: selected[0], endSeq: selected.at(-1) }, sourceEventSeqs: selected });
      const afterCharacters = JSON.stringify(session.deriveMessages()).length;
      const persistence = await checkpoint(ctx, session);
      ctx.logger?.[persistence.durable ? 'info' : 'warn']?.(`parallax handoff ${request.operation_id}: applied; durable=${persistence.durable}; checkpoint=${persistence.persistence_error ?? 'confirmed'}; characters=${beforeCharacters}->${afterCharacters}; session=${session.id}; event=${applied.seq}`);
      if (!persistence.durable) return { ...decision, messages: [...decision.messages, userMessage('The model surface has changed, but persistence is NOT confirmed. Keep the carrier and operation id, report this failure, and query status. Do not clear again or claim durable success.', { operation_id: request.operation_id, state: 'warning' })] };
      return decision;
    } catch (error) {
      // A landed replacement is never rolled back or requested again.
      if (lastRecord(session, request.operation_id)?.data.source.parallax.state === 'applied') throw error;
      await fail(error instanceof Error ? error.message : 'handoff validation failed');
      if (signal.aborted) return decision;
      return { ...decision, messages: [...decision.messages, userMessage(`Handoff ${request.operation_id} failed before replacement. Old context is intact. Check status and report the failure; do not claim a clean context.`, { operation_id: request.operation_id, state: 'notice' })] };
    }
  });
  return () => { active = false; stop(); dispose(); };
}
