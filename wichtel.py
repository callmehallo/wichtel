#!/usr/bin/env python3
import argparse
import base64
import json
import random
import secrets
import subprocess
import sys
import tomllib
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

PAYLOAD_SIZE = 128
MAX_DRAW_ATTEMPTS = 100_000


@dataclass(frozen=True)
class Participant:
    name: str
    phone: str


@dataclass(frozen=True)
class Config:
    participants: list[Participant]
    couples: list[frozenset[str]]
    budget: str
    date: str
    reveal_url: str


def load_config(path: Path) -> Config:
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    participants = [Participant(p["name"], normalize_phone(p["telefon"])) for p in raw["teilnehmer"]]
    names = {p.name for p in participants}
    if len(names) != len(participants):
        sys.exit("Fehler: Namen müssen eindeutig sein.")
    if len(participants) < 3:
        sys.exit("Fehler: Mindestens 3 Teilnehmer nötig.")
    couples = [frozenset(pair) for pair in raw.get("paare", [])]
    for couple in couples:
        unknown = couple - names
        if unknown:
            sys.exit(f"Fehler: Paar enthält unbekannte Namen: {', '.join(sorted(unknown))}")
    return Config(participants, couples, raw["budget"], raw["datum"], raw["reveal_url"])


def normalize_phone(phone: str) -> str:
    digits = "".join(c for c in phone if c.isdigit())
    if phone.strip().startswith("00"):
        digits = digits[2:]
    elif phone.strip().startswith("0"):
        digits = "49" + digits[1:]
    return digits


def draw(participants: list[Participant], couples: list[frozenset[str]]) -> dict[str, str]:
    rng = random.SystemRandom()
    names = [p.name for p in participants]
    for _ in range(MAX_DRAW_ATTEMPTS):
        rng.shuffle(names)
        assignment = {giver: names[(i + 1) % len(names)] for i, giver in enumerate(names)}
        if not any(frozenset((giver, receiver)) in couples for giver, receiver in assignment.items()):
            return assignment
    sys.exit("Fehler: Keine gültige Auslosung möglich – zu viele Paare für zu wenige Leute?")


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def encode_secret(receiver: str, budget: str, date: str) -> str:
    plaintext = json.dumps({"n": receiver, "b": budget, "d": date}, ensure_ascii=False).encode("utf-8")
    if len(plaintext) > PAYLOAD_SIZE:
        sys.exit("Fehler: Name/Budget/Datum zu lang.")
    padded = plaintext.ljust(PAYLOAD_SIZE, b"\0")
    key = secrets.token_bytes(PAYLOAD_SIZE)
    cipher = bytes(a ^ b for a, b in zip(padded, key))
    return f"{b64url(key)}.{b64url(cipher)}"


def decode_secret(token: str) -> dict:
    key_part, cipher_part = (base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)) for part in token.split("."))
    plaintext = bytes(a ^ b for a, b in zip(key_part, cipher_part)).rstrip(b"\0")
    return json.loads(plaintext)


def build_message(giver: Participant, link: str, config: Config) -> str:
    return (
        f"Hallo {giver.name}! 🎄\n\n"
        f"Die Wichtel sind ausgelost. Wen du beschenkst, siehst du hier:\n{link}\n\n"
        f"Budget: {config.budget} · Bescherung: {config.date}\n"
        f"Psst – nicht verraten! 🤫"
    )


def whatsapp_url(phone: str, text: str, use_web: bool) -> str:
    query = urllib.parse.urlencode({"phone": phone, "text": text}, quote_via=urllib.parse.quote)
    if use_web:
        return f"https://api.whatsapp.com/send?{query}"
    return f"whatsapp://send?{query}"


def open_url(url: str) -> None:
    subprocess.run(["open", url], check=True)


def prepare_messages(config: Config) -> list[tuple[Participant, str]]:
    assignment = draw(config.participants, config.couples)
    messages = []
    for giver in config.participants:
        token = encode_secret(assignment[giver.name], config.budget, config.date)
        if decode_secret(token)["n"] != assignment[giver.name]:
            sys.exit("Fehler: Selbsttest der Verschlüsselung fehlgeschlagen.")
        link = f"{config.reveal_url.rstrip('/')}/#{token}"
        messages.append((giver, build_message(giver, link, config)))
    return messages


def send_interactively(messages: list[tuple[Participant, str]], use_web: bool) -> None:
    total = len(messages)
    for index, (giver, text) in enumerate(messages, start=1):
        url = whatsapp_url(giver.phone, text, use_web)
        while True:
            open_url(url)
            answer = input(f"[{index}/{total}] WhatsApp an {giver.name} geöffnet – senden, dann Enter (w = nochmal öffnen): ")
            if answer.strip().lower() != "w":
                break
    print("\nFertig! Alle Lose sind verschickt. 🎁")


def main() -> None:
    parser = argparse.ArgumentParser(description="Wichtel auslosen und per WhatsApp verschicken.")
    parser.add_argument("config", type=Path, nargs="?", default=Path("teilnehmer.toml"))
    parser.add_argument("--web", action="store_true", help="WhatsApp Web statt der Desktop-App öffnen")
    parser.add_argument("--check", action="store_true", help="Nur prüfen, ob eine Auslosung möglich ist – nichts öffnen")
    args = parser.parse_args()

    config = load_config(args.config)
    messages = prepare_messages(config)

    print(f"{len(messages)} Teilnehmer, {len(config.couples)} Paar(e) ausgeschlossen. Auslosung ok.")
    if args.check:
        return
    print("Achtung: Jeder neue Start lost neu aus. Einmal komplett durchziehen!\n")
    input("Enter zum Starten …")
    send_interactively(messages, args.web)


if __name__ == "__main__":
    main()
