import { pgTable, text, bigint, bigserial, integer, primaryKey, index } from 'drizzle-orm/pg-core';

export const conversations = pgTable('conversations', {
  id: text('id').primaryKey(), kind: text('kind').notNull(), name: text('name').notNull(),
  createdAt: bigint('created_at', { mode: 'number' }).notNull(),
});
export const members = pgTable('conversation_members', {
  conversationId: text('conversation_id').notNull().references(() => conversations.id, { onDelete: 'cascade' }),
  username: text('username').notNull(),
}, (t) => [primaryKey({ columns: [t.conversationId, t.username] }), index('member_username_idx').on(t.username)]);
export const messages = pgTable('messages', {
  id: bigserial('id', { mode: 'number' }).primaryKey(),
  conversationId: text('conversation_id').notNull().references(() => conversations.id, { onDelete: 'cascade' }),
  userId: text('user_id').notNull(), text: text('body').notNull(),
  createdAt: bigint('created_at', { mode: 'number' }).notNull(), fileId: text('file_id'),
}, (t) => [index('message_conversation_idx').on(t.conversationId, t.id)]);
export const uploads = pgTable('uploads', {
  id: text('id').primaryKey(), userId: text('user_id').notNull(),
  conversationId: text('conversation_id').notNull().references(() => conversations.id),
  name: text('name').notNull(), size: integer('size').notNull(), text: text('caption').notNull(),
  createdAt: bigint('created_at', { mode: 'number' }).notNull(), messageId: bigint('message_id', { mode: 'number' }).unique(),
});
export const uploadParts = pgTable('upload_parts', {
  uploadId: text('upload_id').notNull().references(() => uploads.id, { onDelete: 'cascade' }),
  part: integer('part').notNull(),
}, (t) => [primaryKey({ columns: [t.uploadId, t.part] })]);
