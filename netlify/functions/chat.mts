import type { Config } from '@netlify/functions';
import { admin, login, logout, getUser, refreshSession, AuthError } from '@netlify/identity';
import { drizzle } from 'drizzle-orm/netlify-db';
import { and, eq, gt, desc, asc, inArray } from 'drizzle-orm';
import { getStore } from '@netlify/blobs';
import { randomUUID, createHash, timingSafeEqual } from 'node:crypto';
import * as schema from '../../db/schema.js';

export function roster(): Record<string, string> {
  return { kyle_guest: "Kyle's Guest", leo: 'Leo', geeker: 'Geeker', fofo: 'Fofo', kyle: 'Kyle' };
}
export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}
export function validUser(value: unknown): value is string {
  return typeof value === 'string' && Object.hasOwn(roster(), value);
}
export function caption(value: unknown, required = true): string {
  if (typeof value !== 'string' || value.trim().length > 2000 || (required && !value.trim())) {
    throw new ApiError(400, 'Message must be 1–2000 characters');
  }
  return value.trim();
}
function profile(username: string) {
  return { user: { id: username, displayName: roster()[username], role: username === 'kyle' ? 'owner' : 'member' },
    users: Object.entries(roster()).map(([id, displayName]) => ({ id, displayName })) };
}
function database() { return drizzle({ schema }); }
function files() { return getStore({ name: 'chat-files', consistency: 'strong' }); }
function reply(data: unknown, status = 200) {
  return Response.json(data, { status, headers: { 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' } });
}
async function body(req: Request) {
  if (!req.headers.get('content-type')?.includes('application/json')) throw new ApiError(415, 'Expected JSON');
  const raw = await req.text();
  if (raw.length > 65536) throw new ApiError(413, 'Request too large');
  try {
    const value = JSON.parse(raw);
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error();
    return value as Record<string, unknown>;
  } catch { throw new ApiError(400, 'Invalid JSON'); }
}
async function signedIn() {
  await refreshSession();
  const user = await getUser();
  const username = user?.appMetadata?.chat_username;
  if (!validUser(username)) throw new ApiError(401, 'Sign in required');
  return username;
}
async function requireMember(db: ReturnType<typeof database>, conversation: unknown, username: string) {
  if (typeof conversation !== 'string') throw new ApiError(400, 'Conversation is required');
  const [member] = await db.select().from(schema.members).where(and(
    eq(schema.members.conversationId, conversation), eq(schema.members.username, username))).limit(1);
  if (!member) throw new ApiError(404, 'Conversation not found');
  return conversation;
}
async function seedDirectMessages(db: ReturnType<typeof database>) {
  const ids = Object.keys(roster()).sort();
  await db.transaction(async (tx) => {
    for (let i = 0; i < ids.length; i++) for (const other of ids.slice(i + 1)) {
      const id = `dm:${ids[i]}:${other}`;
      await tx.insert(schema.conversations).values({ id, kind: 'dm', name: '', createdAt: Date.now() }).onConflictDoNothing();
      await tx.insert(schema.members).values([ids[i], other].map(username => ({ conversationId: id, username }))).onConflictDoNothing();
    }
  });
}
async function signIn(req: Request) {
  const data = await body(req);
  if (!validUser(data.username) || typeof data.password !== 'string' || data.password.length > 1024) {
    throw new ApiError(401, 'Incorrect username or password');
  }
  const username = data.username;
  // Fixed accounts are provisioned only from server-configured passwords, never caller-supplied credentials.
  const email = `${username}@nightwire.invalid`;
  const accounts = await admin.listUsers({ perPage: 100 });
  const existing = accounts.find(user => user.email === email);
  if (!existing) {
    const configured = Netlify.env.get(`CHAT_PASSWORD_${username.toUpperCase()}`);
    if (!configured || configured.length < 8) throw new ApiError(503, 'This account is not configured. Ask the owner to set its server-side account password.');
    const digest = (value: string) => createHash('sha256').update(value).digest();
    if (!timingSafeEqual(digest(configured), digest(data.password))) throw new ApiError(401, 'Incorrect username or password');
    try {
      await admin.createUser({ email, password: configured, data: { app_metadata: { chat_username: username } } });
    } catch {
      // Another invocation may have provisioned the same account; authenticate normally below.
    }
  }
  const identity = await login(email, data.password);
  if (identity?.appMetadata?.chat_username !== username) throw new ApiError(403, 'Account is not authorized for chat');
  await seedDirectMessages(database());
  return reply(profile(username));
}

export default async function handler(req: Request) {
  try {
    const url = new URL(req.url);
    const path = url.pathname;
    if (!['GET', 'POST', 'PUT'].includes(req.method)) return reply({ error: 'Method not allowed' }, 405);
    if (req.method !== 'GET') {
      const origin = req.headers.get('origin');
      if ((origin && origin !== url.origin) || req.headers.get('sec-fetch-site') === 'cross-site') throw new ApiError(403, 'Invalid request origin');
    }
    if (path === '/api/login' && req.method === 'POST') return await signIn(req);
    if (path === '/api/logout' && req.method === 'POST') { await logout(); return reply({ ok: true }); }
    const username = await signedIn();
    if (path === '/api/me' && req.method === 'GET') return reply(profile(username));
    const db = database();
    if (path === '/api/conversations' && req.method === 'GET') {
      const own = await db.select().from(schema.members).where(eq(schema.members.username, username));
      if (!own.length) return reply({ conversations: [] });
      const ids = own.map(m => m.conversationId);
      const rooms = await db.select().from(schema.conversations).where(inArray(schema.conversations.id, ids));
      const people = await db.select().from(schema.members).where(inArray(schema.members.conversationId, ids));
      const entries = await Promise.all(rooms.map(async room => {
        const members = people.filter(m => m.conversationId === room.id).map(m => ({ id: m.username, displayName: roster()[m.username] }));
        const [last] = await db.select().from(schema.messages).where(eq(schema.messages.conversationId, room.id)).orderBy(desc(schema.messages.id)).limit(1);
        return { ...room, members, name: room.kind === 'dm' ? members.find(m => m.id !== username)?.displayName : room.name,
          lastMessage: last?.text ?? null, lastAt: last?.createdAt ?? null };
      }));
      entries.sort((a, b) => (b.lastAt ?? 0) - (a.lastAt ?? 0));
      return reply({ conversations: entries });
    }
    if (path === '/api/groups' && req.method === 'POST') {
      const data = await body(req);
      if (typeof data.name !== 'string' || !data.name.trim() || data.name.trim().length > 40 || !Array.isArray(data.members) || !data.members.every(validUser)) throw new ApiError(400, 'A group name and valid members are required');
      const people = [...new Set([username, ...data.members as string[]])];
      if (people.length < 2) throw new ApiError(400, 'Choose at least one other member');
      const id = `group:${randomUUID()}`;
      await db.transaction(async tx => {
        await tx.insert(schema.conversations).values({ id, kind: 'group', name: (data.name as string).trim(), createdAt: Date.now() });
        await tx.insert(schema.members).values(people.map(username => ({ conversationId: id, username })));
      });
      return reply({ id, name: data.name.trim(), kind: 'group' }, 201);
    }
    if (path === '/api/messages' && req.method === 'GET') {
      const conversation = await requireMember(db, url.searchParams.get('conversation'), username);
      const after = Number(url.searchParams.get('after') || 0);
      if (!Number.isSafeInteger(after) || after < 0) throw new ApiError(400, 'Invalid message cursor');
      const rows = await db.select().from(schema.messages).where(and(eq(schema.messages.conversationId, conversation), gt(schema.messages.id, after)))
        .orderBy(after ? asc(schema.messages.id) : desc(schema.messages.id)).limit(100);
      if (!after) rows.reverse();
      const fileIds = rows.flatMap(m => m.fileId ? [m.fileId] : []);
      const attachments = fileIds.length ? await db.select().from(schema.uploads).where(inArray(schema.uploads.id, fileIds)) : [];
      return reply({ messages: rows.map(m => {
        const attachment = attachments.find(a => a.id === m.fileId);
        return { id: m.id, userId: m.userId, author: roster()[m.userId], text: m.text, createdAt: m.createdAt,
          file: attachment ? { id: attachment.id, name: attachment.name, size: attachment.size } : null };
      }) });
    }
    if (path === '/api/messages' && req.method === 'POST') {
      const data = await body(req);
      const conversationId = await requireMember(db, data.conversationId, username);
      const [message] = await db.insert(schema.messages).values({ conversationId, userId: username, text: caption(data.text), createdAt: Date.now() }).returning();
      return reply({ id: message.id, createdAt: message.createdAt }, 201);
    }
    // Upload in 2 MiB chunks to preserve the 10 MiB limit within Functions' request limit.
    if (path === '/api/uploads' && req.method === 'POST') {
      const data = await body(req);
      const conversationId = await requireMember(db, data.conversationId, username);
      if (typeof data.name !== 'string' || !Number.isInteger(data.size) || Number(data.size) <= 0 || Number(data.size) > 10 * 1024 * 1024) throw new ApiError(400, 'File must be between 1 byte and 10 MB');
      const name = data.name.replaceAll('\\', '/').split('/').pop()!.replace(/[\x00-\x1f\x7f]/g, '').trim().slice(0, 180);
      if (!name) throw new ApiError(400, 'File name is required');
      const id = randomUUID();
      await db.insert(schema.uploads).values({ id, userId: username, conversationId, name, size: Number(data.size), text: caption(data.text ?? '', false), createdAt: Date.now() });
      return reply({ id, chunkSize: 2 * 1024 * 1024 }, 201);
    }
    const partMatch = path.match(/^\/api\/uploads\/([a-f0-9-]{36})\/([0-4])$/);
    if (partMatch && req.method === 'PUT') {
      const [upload] = await db.select().from(schema.uploads).where(and(eq(schema.uploads.id, partMatch[1]), eq(schema.uploads.userId, username))).limit(1);
      if (!upload || upload.messageId || Date.now() - upload.createdAt > 3600000) throw new ApiError(404, 'Upload not found');
      await requireMember(db, upload.conversationId, username);
      const part = Number(partMatch[2]);
      const expected = Math.min(2 * 1024 * 1024, upload.size - part * 2 * 1024 * 1024);
      const bytes = await req.arrayBuffer();
      if (expected <= 0 || bytes.byteLength !== expected) throw new ApiError(400, 'Invalid upload chunk');
      await files().set(`${upload.id}/${part}`, bytes);
      await db.insert(schema.uploadParts).values({ uploadId: upload.id, part }).onConflictDoNothing();
      return reply({ ok: true });
    }
    if (path === '/api/files' && req.method === 'POST') {
      const data = await body(req);
      if (typeof data.uploadId !== 'string') throw new ApiError(400, 'Upload ID is required');
      const [upload] = await db.select().from(schema.uploads).where(and(eq(schema.uploads.id, data.uploadId), eq(schema.uploads.userId, username))).limit(1);
      if (!upload) throw new ApiError(404, 'Upload not found');
      await requireMember(db, upload.conversationId, username);
      const message = await db.transaction(async tx => {
        const [locked] = await tx.select().from(schema.uploads).where(eq(schema.uploads.id, upload.id)).for('update');
        if (locked.messageId) return { id: locked.messageId, createdAt: locked.createdAt };
        const parts = await tx.select().from(schema.uploadParts).where(eq(schema.uploadParts.uploadId, upload.id));
        if (parts.length !== Math.ceil(upload.size / (2 * 1024 * 1024))) throw new ApiError(400, 'Upload is incomplete');
        const [message] = await tx.insert(schema.messages).values({ conversationId: upload.conversationId, userId: username, text: upload.text, fileId: upload.id, createdAt: Date.now() }).returning();
        await tx.update(schema.uploads).set({ messageId: message.id }).where(eq(schema.uploads.id, upload.id));
        return message;
      });
      return reply({ id: message.id, createdAt: message.createdAt }, 201);
    }
    const fileMatch = path.match(/^\/api\/files\/([a-f0-9-]{36})$/);
    if (fileMatch && req.method === 'GET') {
      const [upload] = await db.select().from(schema.uploads).where(eq(schema.uploads.id, fileMatch[1])).limit(1);
      if (!upload?.messageId) throw new ApiError(404, 'File not found');
      await requireMember(db, upload.conversationId, username);
      const store = files();
      let part = 0;
      const stream = new ReadableStream({
        async pull(controller) {
          if (part >= Math.ceil(upload.size / (2 * 1024 * 1024))) { controller.close(); return; }
          const bytes = await store.get(`${upload.id}/${part++}`, { type: 'arrayBuffer' });
          if (!bytes) { controller.error(new Error('File unavailable')); return; }
          controller.enqueue(new Uint8Array(bytes));
        },
      });
      return new Response(stream, { headers: { 'Content-Type': 'application/octet-stream', 'Content-Disposition': `attachment; filename*=UTF-8''${encodeURIComponent(upload.name)}`, 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' } });
    }
    return reply({ error: 'Not found' }, 404);
  } catch (error) {
    if (error instanceof ApiError) return reply({ error: error.message }, error.status);
    if (error instanceof AuthError) return reply({ error: error.status === 400 || error.status === 401 ? 'Incorrect username or password' : 'Authentication is unavailable. Please try again.' }, error.status === 400 ? 401 : error.status || 503);
    // Avoid exposing database connection details or authentication credentials in errors.
    return reply({ error: 'Chat service is unavailable. Please try again shortly.' }, 503);
  }
}
export const config: Config = { path: '/api/*' };
