# Security policy

Rhapsode reads local documents and runs entirely on your machine by default. It does
open sockets, parse untrusted files and download models, so reports about those paths
are welcome.

## Reporting a vulnerability

**Please do not open a public issue for a security problem.** Use GitHub's private
reporting on this repository: **Security -> Report a vulnerability** (Security
Advisories). That opens a private thread with the maintainers.

Include what you did, what happened, the version (`Settings` shows it, or
`/api/health`), and the operating system. A proof of concept helps; a real book or
personal document does not - a small synthetic file is enough.

You can expect an acknowledgement within a few days and an assessment within two
weeks. This is a small project: there is no bug bounty, and fixes ship in the next
beta rather than on a fixed schedule. Please give us a reasonable window before
disclosing publicly.

## Supported versions

Only the newest beta is supported. Releases older than the current `v0.3.6-beta`
do not receive security fixes.

## Scope

- The local HTTP server, including the token guard and the loopback-only default.
- The optional remote access (`MURMUR_HOST` / Settings -> Remote access) and its token.
- Document import (EPUB, PDF, text, URLs) and the OCR path.
- Model and runtime downloads, their pins and checksums.
- The Windows installer and the macOS bundle.

Out of scope: vulnerabilities in third-party models or in upstream projects (report
those upstream), and anything that requires an attacker to already run code as your
user on the same machine.

## What the app sends, and when

There is no telemetry. Network traffic happens only when you ask for something:
the voice model on first start, the optional on-device translator, the stills engine,
ffmpeg in Settings, the optional search model, a URL you paste into Add document, or
a model server you connect. Local download checks and the translation discovery scan
(ports 8091, 8080, 8000, 1234 and 11434 on this machine) never leave the host.
