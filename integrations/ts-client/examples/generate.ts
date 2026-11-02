// Runnable example: node examples/generate.ts [baseUrl] [outDir]
// (Node >= 22.18 / 23.6 runs .ts directly; otherwise `npm run build` and use dist/.)
import { writeFile, mkdir } from "node:fs/promises";
import { join } from "node:path";
import { MidigenClient, MidigenError } from "../src/index.ts";

const base = process.argv[2] ?? process.env.MIDIGEN_URL ?? "http://127.0.0.1:8765";
const outDir = process.argv[3] ?? "out";
const mg = new MidigenClient(base);

const health = await mg.health();
console.log(`service ${base}: ${health.status}, api v${health.api_version}`);
console.log("genres:", (await mg.genres()).map(g => g.id).join(", "));

const song = await mg.generate({ genre: "house", key: "A", mode: "minor", bars: 8, seed: 7 });
const s = song.summary;
console.log(`generated "${s.title}": ${s.bars} bars, ${s.tempo} BPM, ${s.key} ${s.mode}, critic ${s.critic_score}`);
for (const t of s.tracks) console.log(`  ${t.role.padEnd(6)} ch${t.channel + 1} prog ${t.program} — ${t.notes} notes — ${t.name}`);

const edited = await mg.edit(song.spec, { ops: [{ op: "transpose", semitones: 2 }, { op: "set_tempo", bpm: 120 }] });
console.log(`edited: ${edited.summary.key} ${edited.summary.mode} @ ${edited.summary.tempo} BPM`);
const nl = await mg.edit(edited.spec, { instruction: "add strings" });
console.log(`instruction "add strings" -> ops ${JSON.stringify(nl.ops)}; tracks now ${nl.summary.tracks.length}`);

const report = await mg.critique(nl.spec);
console.log(`critique: score ${report.score}, ${report.findings.length} findings`);

const midi = await mg.exportMidi(nl.spec);
await mkdir(outDir, { recursive: true });
const file = join(outDir, "house_edited.mid");
await writeFile(file, midi);
console.log(`wrote ${file} (${midi.length} bytes, header ${new TextDecoder().decode(midi.slice(0, 4))})`);

try {
  await mg.generate({ genre: "house", tempo: 999 });
} catch (e) {
  if (e instanceof MidigenError) console.log(`expected error -> ${e.status} ${e.kind}: ${e.errors[0]}`);
  else throw e;
}
