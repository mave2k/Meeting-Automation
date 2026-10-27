#!/usr/bin/env python3
import sys
import os
import json
import zipfile
import urllib.request
import urllib.parse
import subprocess
import time
from datetime import datetime
 
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL             = "claude-sonnet-4-6"
BEAR_TAG          = "blackwyse/Meeting"
 
SYSTEM_PROMPT = (
    "Du bist ein Assistent, der gesprochene Transkripte praezise zusammenfasst. "
    "Wichtig: Die Eigennahmen 'Blackwyse' und 'Cloudwyse' werden immer mit 'y' geschrieben"
    "korrigiere diese falls sie im Transkript falsch erkannt wurden.\n"
    "Erstelle eine strukturierte Markdown-Zusammenfassung mit:\n"
    "- Einem aussagekraeftigen Titel (# Titel)\n"
    "- Einem kurzen Ueberblick (3-4 Saetze)\n"
    "- Den wichtigsten Punkten als Abschnitte (## Abschnitt)\n"
    "- Einer Abschluss-Sektion mit Kernaussagen oder naechsten Schritten\n\n"
    "Antworte NUR mit dem Markdown-Inhalt, ohne Einleitung oder Erklaerung."
)
 
def read_transcript(path):
    # .whisper Datei (ZIP-Archiv mit metadata.json)
    if path.endswith(".whisper"):
        with zipfile.ZipFile(path, "r") as z:
            with z.open("metadata.json") as f:
                data = json.load(f)
        # Woerter aus allen Transkript-Segmenten zusammensetzen
        words = []
        for segment in data.get("transcripts", []):
            for word in segment.get("words", []):
                words.append(word.get("text", ""))
        return " ".join(words).strip()
    # Fallback: plain text
    with open(path, "r", encoding="utf-8") as f:
        return f.read().strip()
 
def summarize_with_claude(transcript):
    if not ANTHROPIC_API_KEY:
        raise ValueError("ANTHROPIC_API_KEY nicht gesetzt.")
 
    payload = json.dumps({
        "model": MODEL,
        "max_tokens": 2048,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": "Fasse dieses Transkript zusammen:\n\n" + transcript}]
    }).encode("utf-8")
 
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
        },
        method="POST"
    )
 
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read().decode("utf-8"))
 
    return data["content"][0]["text"]
 
def extract_title(markdown):
    for line in markdown.splitlines():
        line = line.strip()
        if line.startswith("# "):
            return datetime.now().strftime("%Y-%m-%d") + " " + line[2:].strip()
    return "Transkript " + datetime.now().strftime("%Y-%m-%d %H:%M")
 
def strip_h1(markdown):
    lines = [l for l in markdown.splitlines() if not l.strip().startswith("# ")]
    return "\n".join(lines).strip()
 
def open_in_bear(title, body, tags):
    subprocess.run(["open", "-a", "Bear"], check=True)
    time.sleep(1.5)
    params = (
        "title=" + urllib.parse.quote(title, safe="") +
        "&text=" + urllib.parse.quote(body, safe="") +
        "&tags=" + urllib.parse.quote(tags, safe="") +
        "&open=yes"
    )
    subprocess.run(["open", "bear://x-callback-url/create?" + params], check=True)
    print("Notiz erstellt: " + title)
 
def main():
    if len(sys.argv) < 2:
        print("Verwendung: python3 summarize_to_bear.py <datei.whisper>")
        sys.exit(1)
 
    path = sys.argv[1]
    if not os.path.isfile(path):
        print("Datei nicht gefunden: " + path)
        sys.exit(1)
 
    print("Lese Transkript...")
    transcript = read_transcript(path)
    if not transcript:
        print("Transkript ist leer.")
        sys.exit(1)
 
    print("Transkript: " + transcript[:100] + "...")
    print("Sende an Claude...")
    markdown = summarize_with_claude(transcript)
 
    title = extract_title(markdown)
    body  = strip_h1(markdown)
    print("Titel: " + title)
 
    open_in_bear(title, body, BEAR_TAG)
 
if __name__ == "__main__":
    main()