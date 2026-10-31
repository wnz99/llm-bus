export interface Env {
  DB: D1Database;
  DELIVERY: Queue<string>;
}
export interface Session {
  address: string;
  machine_id: string;
  repo_id: string;
  native_address: string;
  last_seen: string;
  ended: number;
}
export interface MessageRow {
  sequence: number;
  id: string;
  sender: string;
  recipient: string;
  repo_id: string;
  body: string;
  sent_at: string;
  received_at: string | null;
  acknowledged_at: string | null;
  reply_to: string | null;
}
