# lillycoder

**A local-first coder REPL with file and shell tools. Bring your own LLM.**

`lillycoder` drops you into a chat REPL inside any folder. The model on the other end can read, write, and edit files, run shell commands, install packages, and grep your project. It talks to any OpenAI-compatible `/v1` endpoint, so you pair it with whichever local LLM server you already use (llama.cpp, ollama, LM Studio, etc.). No cloud, no API key, no telemetry.

> ## Disclaimer / no warranty
>
> This software runs an LLM that can read, write, and delete files in the current working directory, run shell commands, and install packages on your behalf. It is provided **as is, without warranty of any kind**, express or implied, including but not limited to merchantability, fitness for a particular purpose, and noninfringement.
>
> By installing or running this software you accept that:
>
> - You alone are responsible for any damage to your data, hardware, or system.
> - The author(s) and contributors are **not liable** for any harm, data loss, security incident, or other damages, however caused.
> - LLMs hallucinate. The model may invent file paths, write incorrect code, or run commands that look right but are not. **Always read the diff and command preview before approving.**
> - The default permission model prompts before each mutation. The `--bypass-permissions` flag skips those prompts. Hard-blocked commands (`sudo`, `rm -rf /`, `mkfs`, fork bombs, etc.) cannot be bypassed by that flag, but custom shell pipelines can still cause damage.
> - Network access from the LLM is whatever your endpoint allows. lillycoder itself does not phone home, but the LLM server you point it at may.
> - Model weights are governed by their own upstream licenses. lillycoder does not download models.
>
> If you do not accept these terms, do not install or run this software.
>
> Full legal license: see [`LICENSE`](LICENSE) (MIT).

## What it is

A small Python CLI. You run `lillycoder` in a project directory and start typing. The model picks tools (read_file, write_file, edit_file, bash, mkdir, mv, rm, grep, find, list_dir, pkg_install) to do what you asked.

Every mutating action is gated:

```
🦊 lilly wants to: write_file("src/index.js", 142 chars)
   [y]es  [n]o  [a]lways for this tool  [p]ath: always for this exact target
   >
```

The hard-deny safety list runs on top of that and cannot be turned off by the permission flag: `sudo`, `rm -rf /`, `mkfs`, `dd of=/dev/*`, fork bombs, recursive chmod/chown of `/` or `~`. They are refused at the safety classifier before exec.

## What it is NOT

`lillycoder` does not start LLM servers, manage Docker, or ship a model. It expects a server to be running already at, say, `http://localhost:11434/v1` (ollama) or `http://localhost:8080/v1` (llama.cpp). On first run it scans common ports and offers to use whatever it finds; you can also pass `--api http://your.url`.

## Install

### One-liner (Debian / Ubuntu)

```sh
curl -fsSL https://raw.githubusercontent.com/ra-yavuz/lillycoder/main/scripts/get.sh | sudo bash
```

This adds the `ra-yavuz` apt repository (signing key + sources list), then `apt install lillycoder`. Re-running it is safe.

If you would rather read the script first (recommended for any `curl | bash`):

```sh
curl -fsSL https://raw.githubusercontent.com/ra-yavuz/lillycoder/main/scripts/get.sh -o get.sh
less get.sh
sudo bash get.sh
```

### Manual apt steps (Debian / Ubuntu)

If you prefer to wire up the apt repo by hand:

```sh
sudo install -d -m 0755 /etc/apt/keyrings
curl -fsSL https://ra-yavuz.github.io/apt/pubkey.gpg \
  | sudo tee /etc/apt/keyrings/ra-yavuz.gpg >/dev/null
echo "deb [signed-by=/etc/apt/keyrings/ra-yavuz.gpg] https://ra-yavuz.github.io/apt stable main" \
  | sudo tee /etc/apt/sources.list.d/ra-yavuz.list
sudo apt update
sudo apt install lillycoder
```

### From source (any Linux)

```sh
git clone https://github.com/ra-yavuz/lillycoder.git
cd lillycoder
pip install --user -e .
```

## Quick start

Have an LLM server running somewhere on localhost. Then in any project:

```sh
cd ~/myproject
lillycoder
```

Output:

```
🦊 scanning localhost for LLM servers...
🦊 found 1 endpoint: http://localhost:11434/v1 (ollama, 3 models)
   use it? [Y/n] y
✓ ollama · qwen2.5-coder:7b
🦊 lilly is awake · qwen2.5-coder:7b · /home/you/myproject  ·  11 tools
   type a message · /help for commands · /exit to leave
[ctx 1.2k/8k·15%] › what files are in this folder?
```

Or skip discovery and point at a known endpoint:

```sh
lillycoder --api http://localhost:8080/v1
```

## Slash commands

| command       | what it does |
|---            |---|
| `/help`       | show commands |
| `/tools`      | list tools the model can call |
| `/persona`    | show the active persona |
| `/clear`      | wipe conversation, keep persona |
| `/compact`    | summarise older history into a system note |
| `/exit`       | leave (or `Ctrl+D`) |

## Personas

The bundled persona is a kid-coder voice. To use a different one, drop a markdown file at `~/.config/lillycoder/personas/<name>.md` and run `lillycoder --persona <name>`.

```sh
lillycoder --list-personas
lillycoder --persona terse-senior
```

## Compatible servers

Anything speaking the OpenAI `/v1/chat/completions` shape works. Tested:

- `llama.cpp` (`llama-server`)
- `ollama` (`/v1` compatibility surface)
- `LM Studio` (local server)
- `hydra-llm` (also publishes /v1)

The model on the other end matters: tool-calling reliability needs a model trained for it. lillycoder warns if the chosen model is not in its known-tool-capable allowlist (Qwen 2.5+, Qwen 3, Gemma 3+, Llama 3.1+, Mistral Small 3, Dolphin 3 R1). Pass `--force` to silence the warning.

## Pairs with hydra-llm

[hydra-llm](https://github.com/ra-yavuz/hydra-llm) is a sibling project that manages local LLM servers: it wraps llama.cpp in Docker, ships a curated GGUF catalog with anonymous downloads, and exposes each running model as an OpenAI-compatible endpoint on a stable local port. lillycoder talks that exact shape, so the two compose into a fully local coding agent in one terminal:

```sh
# in hydra-llm:
hydra-llm start qwen2.5-32b           # or any 'code' tagged model from list-online
hydra-llm api   qwen2.5-32b           # prints the URL

# in your project directory:
lillycoder --api http://localhost:18087/v1
# (lilly auto-detects common local LLM ports, so just `lillycoder` often works)
```

hydra-llm handles model lifecycle (download, start/stop, system prompts, persistent sessions, optional KDE Plasma 6 panel widget). lillycoder is the agent on top: file tools, shell tools, grep, permission gating. Use them together, or use lillycoder with whatever local server you already run.

## Development

The repo includes `docker-compose.yml` for an isolated dev sandbox: it mounts `WORKINGDIR/` from the host into a container that already has `lillycoder` installed. Edit on host, run inside container:

```sh
docker compose up -d
docker compose exec lillycoder bash
# inside the container, in /workspace:
lillycoder --api http://host.docker.internal:11434/v1
```

If she goes haywire, damage stays inside `WORKINGDIR/` on the host.

## Layout

```
lillycoder/
  bin/lillycoder              CLI shim
  lib/lillycoder/             package
    agent.py                  turn loop, tool dispatch
    discovery.py              scans localhost for /v1 endpoints
    endpoint.py               connection layer
    config.py                 XDG config + personas
    context.py                token estimate, /compact, autocompact
    permissions.py            per-tool [y/n/always] prompts
    safety.py                 hard-deny classifier
    repl.py                   prompt_toolkit loop
    tools/                    one file per tool
    persona/default.md        bundled lilly-coder persona
  debian/                     debian packaging
  docs/index.html             project Pages site
  scripts/build-deb.sh        portable .deb build (no debhelper)
  WORKINGDIR/                 mounted into the dev container
```

## Author

Ramazan Yavuz: [ramazan-yavuz.tr](https://ramazan-yavuz.tr) / [ra-yavuz on GitHub](https://github.com/ra-yavuz)
