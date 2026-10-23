CREATE TABLE "conversations" (
	"id" text PRIMARY KEY,
	"kind" text NOT NULL,
	"name" text NOT NULL,
	"created_at" bigint NOT NULL
);
--> statement-breakpoint
CREATE TABLE "conversation_members" (
	"conversation_id" text,
	"username" text,
	CONSTRAINT "conversation_members_pkey" PRIMARY KEY("conversation_id","username")
);
--> statement-breakpoint
CREATE TABLE "messages" (
	"id" bigserial PRIMARY KEY,
	"conversation_id" text NOT NULL,
	"user_id" text NOT NULL,
	"body" text NOT NULL,
	"created_at" bigint NOT NULL,
	"file_id" text
);
--> statement-breakpoint
CREATE TABLE "upload_parts" (
	"upload_id" text,
	"part" integer,
	CONSTRAINT "upload_parts_pkey" PRIMARY KEY("upload_id","part")
);
--> statement-breakpoint
CREATE TABLE "uploads" (
	"id" text PRIMARY KEY,
	"user_id" text NOT NULL,
	"conversation_id" text NOT NULL,
	"name" text NOT NULL,
	"size" integer NOT NULL,
	"caption" text NOT NULL,
	"created_at" bigint NOT NULL,
	"message_id" bigint UNIQUE
);
--> statement-breakpoint
CREATE INDEX "member_username_idx" ON "conversation_members" ("username");--> statement-breakpoint
CREATE INDEX "message_conversation_idx" ON "messages" ("conversation_id","id");--> statement-breakpoint
ALTER TABLE "conversation_members" ADD CONSTRAINT "conversation_members_conversation_id_conversations_id_fkey" FOREIGN KEY ("conversation_id") REFERENCES "conversations"("id") ON DELETE CASCADE;--> statement-breakpoint
ALTER TABLE "messages" ADD CONSTRAINT "messages_conversation_id_conversations_id_fkey" FOREIGN KEY ("conversation_id") REFERENCES "conversations"("id") ON DELETE CASCADE;--> statement-breakpoint
ALTER TABLE "upload_parts" ADD CONSTRAINT "upload_parts_upload_id_uploads_id_fkey" FOREIGN KEY ("upload_id") REFERENCES "uploads"("id") ON DELETE CASCADE;--> statement-breakpoint
ALTER TABLE "uploads" ADD CONSTRAINT "uploads_conversation_id_conversations_id_fkey" FOREIGN KEY ("conversation_id") REFERENCES "conversations"("id");