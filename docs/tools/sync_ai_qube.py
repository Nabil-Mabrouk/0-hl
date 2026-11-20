"""Copy the 0-hl specification (docs/…/grok-bot-bible-v2) into the ai-qube Brain project `0-hl`.

The repository stays the single source of truth; ai-qube holds a read copy used to follow
the development. One note per chapter (role `chapter`, ordered like the sommaire). A
manifest next to this script maps each source file to its note path and content hash, so
a later run only updates what changed.

Usage:  python docs/tools/sync_ai_qube.py [--dry-run] [--force]
Token:  $AIQUBE_TOKEN, otherwise read from the local Claude Code MCP config (~/.claude.json,
        server `ai-qube`). It is never written to the repository.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

URL = "https://api.ai-qube.com/mcp/"
PROJECT = "0-hl"
FOLDER = "02-projects/0-hl/spec"
ROOT = Path(__file__).resolve().parents[2]
BIBLE = ROOT / "docs" / "GROK_BOT_TERMINAL_BIBLE_V2_COMPLETE" / "grok-bot-bible-v2"
MANIFEST = Path(__file__).with_name("ai_qube_manifest.json")

# Notes created by hand on 2026-11-17 (V2.0 copies): updated in place, never duplicated.
LEGACY = {
    "00-guide/00-lecture.md": "chapitre-1.md",
    "00-guide/01-sommaire.md": "chapitre-2.md",
    "01-produit/01-vision-et-perimetre.md": "chapitre-3.md",
    "01-produit/02-personas-parcours-gouvernance.md": "chapitre-4.md",
    "02-design/03-design-system.md": "chapitre-5.md",
    "02-design/04-animation-interactions.md": "chapitre-6.md",
    "03-ecrans/UI-01-live-terminal.md": "chapitre-7.md",
}


def chapter_order() -> list[str]:
    """00-lecture, sommaire, then the sommaire's chapter list, then README."""
    sommaire = (BIBLE / "00-guide" / "01-sommaire.md").read_text(encoding="utf-8")
    section = sommaire.split("## Chapitres", 1)[1].split("\n## ", 1)[0]
    out = ["00-guide/00-lecture.md", "00-guide/01-sommaire.md"]
    for link in re.findall(r"\]\(([^)]+\.md)\)", section):
        rel = (BIBLE / "00-guide" / link).resolve().relative_to(BIBLE.resolve()).as_posix()
        if rel not in out:
            out.append(rel)
    for p in sorted(BIBLE.rglob("*.md")):               # anything the sommaire forgot
        rel = p.relative_to(BIBLE).as_posix()
        if not rel.startswith("original/") and rel not in out:
            out.append(rel)
    return out


def token() -> str:
    if os.environ.get("AIQUBE_TOKEN"):
        return os.environ["AIQUBE_TOKEN"]
    cfg = json.loads((Path.home() / ".claude.json").read_text(encoding="utf-8"))
    for key, proj in (cfg.get("projects") or {}).items():
        if Path(key).resolve() == ROOT:
            auth = proj.get("mcpServers", {}).get("ai-qube", {}).get("headers", {}).get("Authorization", "")
            if auth.startswith("Bearer "):
                return auth[7:]
    sys.exit("jeton ai-qube introuvable : définir AIQUBE_TOKEN ou configurer le serveur MCP `ai-qube`")


class Mcp:
    def __init__(self, bearer: str) -> None:
        self.bearer, self.session, self.n = bearer, None, 0

    def _post(self, payload: dict) -> dict | None:
        headers = {"Authorization": f"Bearer {self.bearer}", "Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream"}
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        req = urllib.request.Request(URL, data=json.dumps(payload).encode(), headers=headers)
        with urllib.request.urlopen(req, timeout=60) as resp:
            self.session = resp.headers.get("Mcp-Session-Id") or self.session
            raw = resp.read().decode("utf-8")
        if not raw.strip():
            return None
        if raw.lstrip().startswith("{"):
            return json.loads(raw)
        data = [line[5:].strip() for line in raw.splitlines() if line.startswith("data:")]
        return json.loads(data[-1]) if data else None

    def start(self) -> None:
        self.request("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                                    "clientInfo": {"name": "0-hl-spec-sync", "version": "1"}})
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def request(self, method: str, params: dict) -> dict:
        self.n += 1
        msg = self._post({"jsonrpc": "2.0", "id": self.n, "method": method, "params": params})
        if msg is None or "error" in msg:
            raise RuntimeError(f"{method}: {msg and msg.get('error')}")
        return msg["result"]

    def tool(self, name: str, args: dict) -> dict:
        res = self.request("tools/call", {"name": name, "arguments": args})
        text = "".join(c.get("text", "") for c in res.get("content", []) if c.get("type") == "text")
        if res.get("isError"):
            raise RuntimeError(f"{name}: {text}")
        try:
            return json.loads(text) if text else {}
        except ValueError:
            return {"text": text}


def git_sha() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True,
                              text=True, check=True).stdout.strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def title_of(text: str, rel: str) -> str:
    m = re.search(r"^#\s+(.+)$", text, re.M)
    return m.group(1).strip() if m else rel


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="réécrire même sans changement")
    args = ap.parse_args()
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else {}
    order = chapter_order()
    sha = git_sha()
    mcp = None if args.dry_run else Mcp(token())
    if mcp:
        mcp.start()
    created = updated = unchanged = 0
    for i, rel in enumerate(order, 1):
        text = (BIBLE / rel).read_text(encoding="utf-8")
        digest = hashlib.sha256(text.encode()).hexdigest()
        entry = manifest.get(rel, {})
        path = entry.get("path") or LEGACY.get(rel)
        if path and entry.get("sha256") == digest and entry.get("order") == i and not args.force:
            unchanged += 1
            continue
        title = title_of(text, rel)
        body = (f"> Copie de lecture du dépôt 0-hl : `{rel}` (commit {sha}). "
                f"La source de vérité reste le dépôt ; ne pas modifier cette note.\n\n{text}")
        fm = {"title": title, "order": i, "role": "chapter", "project": PROJECT, "source": rel,
              "source_commit": sha}
        if args.dry_run:
            print(f"{'update' if path else 'create'} {i:>2} {rel} -> {path or '(nouvelle note)'}")
            continue
        assert mcp is not None
        if path:
            mcp.tool("brain_update_note", {"path": path, "body": body, "frontmatter_patch": fm})
            updated += 1
        else:
            res = mcp.tool("brain_create_note", {"title": title, "body": body, "folder": FOLDER,
                                                  "project": PROJECT, "role": "chapter", "order": i,
                                                  "category": "spec", "tags": ["spec", "0-hl"]})
            path = res.get("path") or (res.get("note") or {}).get("path")
            if not path:
                raise RuntimeError(f"création de {rel} : chemin absent de la réponse {res}")
            mcp.tool("brain_update_note", {"path": path, "frontmatter_patch": fm})
            created += 1
        manifest[rel] = {"path": path, "sha256": digest, "order": i}
        MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"{i:>2} {rel} -> {path}")
    print(f"{len(order)} chapitres : {created} créés, {updated} mis à jour, {unchanged} inchangés")
    return 0


if __name__ == "__main__":
    sys.exit(main())
