// T-D4 — no hard-coded colour outside the token file (spec 02-design/03 « Tokens », ADR-028).
// Reads the files from disk (Vitest does not serve CSS contents to import.meta.glob).
import { describe, expect, it } from "vitest";

type Fs = { readFileSync(p: string, e: string): string; readdirSync(p: string): string[]; statSync(p: string): { isFile(): boolean } };
const COLOUR = /#[0-9a-fA-F]{3,8}\b|\brgba?\(\s*\d+|\bhsla?\(\s*\d/g;
// allowed: neutral black shadows/overlays (rgba(0, …) / rgba(5, …)), not palette colours
const NEUTRAL = /^rgba?\(\s*(0|5)$/;
const SCANNED = ["features", "domain", "components/layout", "landing.css"];

describe("design tokens", () => {
  it("feature styles and components use tokens, never literal colours", async () => {
    const fsName = "node:fs";
    const fs = (await import(/* @vite-ignore */ fsName)) as Fs;
    const root = `${(globalThis as unknown as { process: { cwd(): string } }).process.cwd()}/src`;
    const walk = (p: string): string[] => (fs.statSync(`${root}/${p}`).isFile() ? [p]
      : fs.readdirSync(`${root}/${p}`).flatMap((n) => walk(`${p}/${n}`)));
    const files = SCANNED.flatMap(walk).filter((f) => /\.(css|tsx?)$/.test(f) && !/\.test\.tsx?$/.test(f));
    expect(files.filter((f) => f.endsWith(".css")).length).toBeGreaterThan(3);
    const offenders: string[] = [];
    for (const f of files) {
      fs.readFileSync(`${root}/${f}`, "utf-8").split("\n").forEach((line, i) => {
        const code = line.replace(/\/\*.*?\*\/|\/\/.*$/g, "");
        const hits = (code.match(COLOUR) ?? []).filter((h) => !NEUTRAL.test(h));
        if (hits.length) offenders.push(`${f}:${i + 1} ${hits.join(" ")}`);
      });
    }
    expect(offenders).toEqual([]);
  });
});
