/* Storage for the judge relay.
 *
 * Serverless functions do not share memory, so a judge's tap and the plugin's
 * poll almost never land on the same instance: everything a room knows lives
 * in Postgres. Two tables are enough - a key/value table for room metadata and
 * state, and an append-only queue for judge presses.
 *
 * With no DATABASE_URL configured the whole thing falls back to a Map so
 * `next dev` and preview builds run without provisioning anything. That
 * fallback is single-instance and is never a production backend.
 */
import { neon } from '@neondatabase/serverless';

const url =
  process.env.DATABASE_URL ??
  process.env.POSTGRES_URL ??
  process.env.DATABASE_URL_UNPOOLED ??
  process.env.POSTGRES_URL_NON_POOLING;

export const usingDatabase = Boolean(url);

const sql = usingDatabase ? neon(url!) : null;

/* ---------------------------------------------------------------- schema */

let ready: Promise<void> | null = null;

/** Created on first use; a race between cold starts is harmless. */
function ensureSchema(): Promise<void> {
  if (!sql) return Promise.resolve();
  if (!ready) {
    ready = (async () => {
      await sql`create table if not exists kc_kv (
        k text primary key,
        v jsonb not null,
        expires_at timestamptz not null
      )`;
      await sql`create table if not exists kc_cmd (
        id bigserial primary key,
        room text not null,
        cmd jsonb not null,
        expires_at timestamptz not null
      )`;
      await sql`create index if not exists kc_cmd_room_id on kc_cmd (room, id)`;
    })().catch((err) => {
      ready = null; // let the next request try again
      throw err;
    });
  }
  return ready;
}

/* ---------------------------------------------------------------- memory */

type Entry = { value: unknown; expires: number };
const mem = new Map<string, Entry>();

function memGet(key: string): unknown {
  const hit = mem.get(key);
  if (!hit) return null;
  if (hit.expires < Date.now()) {
    mem.delete(key);
    return null;
  }
  return hit.value;
}

function memSet(key: string, value: unknown, ttlSec: number): void {
  mem.set(key, { value, expires: Date.now() + ttlSec * 1000 });
}

/* ------------------------------------------------------------------- api */

export async function get<T>(key: string): Promise<T | null> {
  if (!sql) return (memGet(key) as T) ?? null;
  await ensureSchema();
  const rows = await sql`select v from kc_kv where k = ${key} and expires_at > now()`;
  return rows.length ? (rows[0].v as T) : null;
}

export async function set(key: string, value: unknown, ttlSec: number): Promise<void> {
  if (!sql) return memSet(key, value, ttlSec);
  await ensureSchema();
  await sql`
    insert into kc_kv (k, v, expires_at)
    values (${key}, ${JSON.stringify(value)}::jsonb, now() + make_interval(secs => ${ttlSec}))
    on conflict (k) do update set v = excluded.v, expires_at = excluded.expires_at`;
}

export async function del(key: string): Promise<void> {
  if (!sql) {
    mem.delete(key);
    return;
  }
  await ensureSchema();
  await sql`delete from kc_kv where k = ${key}`;
}

/** Append a judge press. `cap` bounds the queue so a timer that went away
 *  cannot leave a room growing without limit. */
export async function push(key: string, value: unknown, ttlSec: number, cap: number): Promise<void> {
  if (!sql) {
    const list = ((memGet(key) as unknown[]) ?? []).concat(value);
    memSet(key, list.slice(-cap), ttlSec);
    return;
  }
  await ensureSchema();
  await sql`
    insert into kc_cmd (room, cmd, expires_at)
    values (${key}, ${JSON.stringify(value)}::jsonb, now() + make_interval(secs => ${ttlSec}))`;
  await sql`
    delete from kc_cmd
    where room = ${key}
      and (expires_at <= now()
           or id <= coalesce((select min(id) from (
                select id from kc_cmd where room = ${key} order by id desc limit ${cap}
              ) recent), 0) - 1)`;
}

/** Take up to `max` queued presses and remove them in the same statement, so
 *  two overlapping polls can never replay the same tap. */
export async function drain<T>(key: string, max: number): Promise<T[]> {
  if (!sql) {
    const list = (memGet(key) as T[]) ?? [];
    if (list.length === 0) return [];
    const taken = list.slice(0, max);
    memSet(key, list.slice(max), 60 * 60);
    return taken;
  }
  await ensureSchema();
  const rows = await sql`
    delete from kc_cmd
    where id in (
      select id from kc_cmd
      where room = ${key} and expires_at > now()
      order by id
      limit ${max}
    )
    returning cmd, id`;
  return rows.sort((a, b) => Number(a.id) - Number(b.id)).map((row) => row.cmd as T);
}
