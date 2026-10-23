---
name: Bug Report
about: Report a bug or unexpected behavior
labels: bug
---

## Description

A clear description of what the bug is.

## Steps to Reproduce

1. ...
2. ...
3. ...

## Expected Behavior

What you expected to happen.

## Actual Behavior

What actually happened. Include error messages or screenshots if applicable.

## Environment

- OS: (e.g., macOS 15, Ubuntu 24.04)
- Python: (e.g., 3.11.8)
- Install method: (Mac app, pipx/pip, Docker, editable)
- bpp version: (e.g., 0.2.0; Mac app: About → version)

## Logs

Paste the last ~200 lines of `<your library>/logs/server.log` (tokens are
already redacted; the lines may show your user name in paths — trim them if
you like) and, if the app is running, the output of
`http://127.0.0.1:5001/api/v1/health` opened in the app's own window.
