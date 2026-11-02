/**
 * Typed client for the midigen REST service (integrations/python/midigen_integrations/service.py).
 * Zero dependencies: uses the global `fetch` (Node >= 18, browsers, Deno, Bun).
 *
 *   const mg = new MidigenClient("http://127.0.0.1:8765");
 *   const song = await mg.generate({ genre: "house", bars: 16, seed: 7 });
 *   const midi = await mg.exportMidi(song.spec);          // Uint8Array, a Standard MIDI File
 */

/** A midigen song spec. Treat as opaque JSON; its schema is GET /options -> spec_schema. */
export type Spec = Record<string, unknown> & { key: string; mode: string; tempo: number };

export interface GenerateParams {
  prompt?: string;
  genre?: string;
  key?: string;
  mode?: string;
  /** BPM, 20-300 */
  tempo?: number;
  /** total bars, 1-256 */
  bars?: number;
  seed?: number;
  refine?: boolean;
  title?: string;
  use_llm?: boolean;
}

export interface TrackSummary { name: string; role: string; channel: number; program: number; notes: number }
export interface SectionSummary { name: string; bars: number; energy: number | null; chords: string[] }

export interface SongSummary {
  title: string | null; genre: string | null; key: string; mode: string; tempo: number;
  time_signature: [number, number]; bars: number; total_beats: number; duration_seconds: number;
  seed: number | null; sections: SectionSummary[]; arrangement: string[]; tracks: TrackSummary[];
  critic_score: number;
}

export interface CriticFinding { check: string; severity: string; message: string; [k: string]: unknown }
export interface CriticReport { score: number; findings: CriticFinding[]; stats: Record<string, unknown> }

export interface Song {
  spec: Spec; summary: SongSummary; critic: CriticReport; source: string;
  ops: Record<string, unknown>[]; midi_base64?: string;
}

export interface GenreInfo {
  id: string; display: string; description: string;
  tempo: { min: number; max: number; default: number }; modes: string[];
}

export type EditOp = { op: string; [k: string]: unknown };

export interface RenderedNotes {
  tempo_qpm: number; time_signature: [number, number]; total_beats: number;
  sections: { name: string; start: number; end: number; energy: number }[];
  tracks: { name: string; role: string; channel: number; program: number; volume: number; pan: number;
            is_drums: boolean; notes: [start: number, dur: number, pitch: number, velocity: number][] }[];
}

export class MidigenError extends Error {
  readonly status: number;
  readonly kind: string;
  readonly errors: string[];
  constructor(status: number, kind: string, errors: string[]) {
    super(`midigen ${status} ${kind}: ${errors.join("; ")}`);
    this.name = "MidigenError";
    this.status = status; this.kind = kind; this.errors = errors;
  }
}

export interface ClientOptions { fetch?: typeof fetch; headers?: Record<string, string>; timeoutMs?: number }

export class MidigenClient {
  private readonly base: string;
  private readonly f: typeof fetch;
  private readonly headers: Record<string, string>;
  private readonly timeoutMs: number;

  constructor(baseUrl = "http://127.0.0.1:8765", opts: ClientOptions = {}) {
    this.base = baseUrl.replace(/\/+$/, "");
    this.f = opts.fetch ?? globalThis.fetch.bind(globalThis);
    this.headers = opts.headers ?? {};
    this.timeoutMs = opts.timeoutMs ?? 60_000;
  }

  health(): Promise<{ status: string; api_version: string; midigen_version: string }> { return this.json("GET", "/health"); }
  options(): Promise<Record<string, unknown>> { return this.json("GET", "/options"); }
  genres(): Promise<GenreInfo[]> { return this.json("GET", "/genres"); }
  genre(name: string): Promise<Record<string, unknown>> { return this.json("GET", `/genres/${encodeURIComponent(name)}`); }

  /** Generate a song (JSON with spec, summary, critic). */
  generate(params: GenerateParams): Promise<Song> { return this.json("POST", "/generate", params); }
  /** Generate and return the .mid bytes directly. */
  generateMidi(params: GenerateParams): Promise<Uint8Array> { return this.bytes("/generate?format=midi", params); }

  /** Apply JSON ops or a plain-English instruction; returns the edited song. */
  edit(spec: Spec, change: { ops: EditOp[] } | { instruction: string }, use_llm = false): Promise<Song> {
    return this.json("POST", "/edit", { spec, ...change, use_llm });
  }
  critique(spec: Spec): Promise<CriticReport> { return this.json("POST", "/critique", { spec }); }
  exportMidi(spec: Spec): Promise<Uint8Array> { return this.bytes("/export?format=midi", { spec }); }
  exportNotes(spec: Spec): Promise<RenderedNotes> { return this.json("POST", "/export?format=notes", { spec }); }

  // ------------------------------------------------------------------ transport
  private async request(method: string, path: string, body?: unknown): Promise<Response> {
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), this.timeoutMs);
    try {
      const res = await this.f(this.base + path, {
        method, signal: ctl.signal,
        headers: { ...(body === undefined ? {} : { "content-type": "application/json" }), ...this.headers },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      if (!res.ok) {
        let kind = "http_error", errors = [res.statusText];
        try { const j = await res.json() as { error?: string; errors?: string[] }; kind = j.error ?? kind; errors = j.errors ?? errors; } catch { /* not JSON */ }
        throw new MidigenError(res.status, kind, errors);
      }
      return res;
    } finally {
      clearTimeout(timer);
    }
  }
  private async json<T>(method: string, path: string, body?: unknown): Promise<T> {
    return (await this.request(method, path, body)).json() as Promise<T>;
  }
  private async bytes(path: string, body: unknown): Promise<Uint8Array> {
    return new Uint8Array(await (await this.request("POST", path, body)).arrayBuffer());
  }
}
