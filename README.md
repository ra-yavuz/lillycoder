# lillycoder

**A local-first coder REPL with a persona that evolves. Bring your own LLM.**

`lillycoder` drops you into a chat REPL inside any folder. The model on the other end can read, write, and edit files, run shell commands, install packages, and grep your project. It talks to any OpenAI-compatible `/v1` endpoint, so you pair it with whichever local LLM server you already use (llama.cpp, ollama, LM Studio, [hydra-llm](https://ra-yavuz.github.io/hydra-llm/)). No cloud. No API key. No telemetry. No account.

What sets lillycoder apart from other coder agents:

- **It has a personality, and the personality is yours.** Six bundled personas (default kid coder, tsundere, yandere, sweet, calm-adult, analytical), live switching with `/personalities load <name>`, copy-on-write user shadows, and a `/personalities diff` to see how your fork drifted from the upstream version after an update.
- **The persona evolves.** Ask Lilly to rewrite her own persona, flip `/persona-evolve on`, and the current shape gets snapshotted to disk and refined across sessions. The next time you launch, she comes back as the version you grew, not the bundled default.
- **Lilly manages her own personalities.** `add_persona`, `clone_persona`, `set_active_persona`, `set_evolve` are real tools the model can call. Tell her "make a pirate persona and switch to it" and she does it through tool calls, not by writing files into your repo.
- **Smart token budgeting on thinking models.** `auto` mode is computed from your model's actual context window, so reasoning models get enough headroom to finish thinking AND emit visible content. Override with `/max-tokens <n>` whenever you want a hard cap.
- **All local. Always.** lillycoder runs against any OpenAI-compatible server you point it at. It does not phone home, does not require an account, and does not depend on any cloud service.

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

A small Python CLI. You run `lillycoder` in a project directory and start typing. The model picks tools (`read_file`, `write_file`, `edit_file`, `bash`, `mkdir`, `mv`, `rm`, `grep`, `find`, `list_dir`, `pkg_install`, plus the persona-admin tools) to do what you asked.

Every mutating action is gated:

```
🦊 lilly wants to: write_file("src/index.js", 142 chars)
   [y]es  [n]o  [a]lways for this tool  [p]ath: always for this exact target
   >
```

The hard-deny safety list runs on top of that and cannot be turned off by the permission flag: `sudo`, `rm -rf /`, `mkfs`, `dd of=/dev/*`, fork bombs, recursive chmod/chown of `/` or `~`. They are refused at the safety classifier before exec.

## What it is NOT

`lillycoder` does not start LLM servers, manage Docker, or ship a model. It expects a server to be running already at, say, `http://localhost:11434/v1` (ollama) or `http://localhost:8080/v1` (llama.cpp). On first run it scans common ports and offers to use whatever it finds; you can also pass `--api http://your.url`.

For the server side, see [hydra-llm](https://ra-yavuz.github.io/hydra-llm/) (sibling project, also under `ra-yavuz`).

## Install

### One line (Debian / Ubuntu)

Sets up the signed `ra-yavuz` apt repo if not already added, refreshes the package index, and installs lillycoder. Idempotent, safe to re-run:

```sh
sudo bash -c 'set -e; install -m 0755 -d /etc/apt/keyrings && curl -fsSL https://ra-yavuz.github.io/apt/pubkey.gpg -o /etc/apt/keyrings/ra-yavuz.gpg && echo "deb [signed-by=/etc/apt/keyrings/ra-yavuz.gpg] https://ra-yavuz.github.io/apt stable main" > /etc/apt/sources.list.d/ra-yavuz.list && apt update && apt install -y lillycoder'
```

If you already added the `ra-yavuz` apt repo earlier, all you need is:

```sh
sudo apt update && sudo apt install lillycoder
```

The `sudo apt update` step is required: without it apt will not see new packages or new versions.

### One line via the bundled installer script

Equivalent to the above, with extra prerequisite checks and a friendlier output summary:

```sh
curl -fsSL https://raw.githubusercontent.com/ra-yavuz/lillycoder/main/scripts/get.sh | sudo bash
```

If you would rather read the script first (recommended for any `curl | bash`):

```sh
curl -fsSL https://raw.githubusercontent.com/ra-yavuz/lillycoder/main/scripts/get.sh -o get.sh
less get.sh
sudo bash get.sh
```

### Step by step (manual repo setup)

```sh
# 1. Trust the signing key
sudo install -d -m 0755 /etc/apt/keyrings
curl -fsSL https://ra-yavuz.github.io/apt/pubkey.gpg \
  | sudo tee /etc/apt/keyrings/ra-yavuz.gpg >/dev/null

# 2. Add the apt source
echo "deb [signed-by=/etc/apt/keyrings/ra-yavuz.gpg] https://ra-yavuz.github.io/apt stable main" \
  | sudo tee /etc/apt/sources.list.d/ra-yavuz.list

# 3. Refresh the package index, then install
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
🦊 lilly is awake in /home/you/myproject · /help · /exit · ctrl+d to leave · ctrl+c twice
🦊 qwen2.5-coder:7b · 1% of 8k · default · max:auto
› what files are in this folder?
```

Or skip discovery and point at a known endpoint:

```sh
lillycoder --api http://localhost:8080/v1
```

## Slash commands

| command                              | what it does |
|---                                   |---|
| `/help`                              | show all commands |
| `/tools`                             | list tools the model can call |
| `/clear`                             | wipe conversation, keep persona |
| `/compact`                           | summarise older history into a system note |
| `/exit`                              | leave (or `Ctrl+D`) |
| `/persona`                           | show the current persona text |
| `/persona-active`                    | which persona is loaded right now (name + origin + path) |
| `/persona-copy <src> <dst>`          | clone a persona under a new user-owned name |
| `/personas`                          | list saved personas (alias for `/personalities list`) |
| `/setpersona <name\|-f path\|text>`  | switch by name, by file, or by inline text |
| `/personalities list`                | every available persona with origin tags (bundled / user) |
| `/personalities load <name>`         | switch the active persona by name |
| `/personalities show <name>`         | print a persona's full text |
| `/personalities add <name> ...`      | save a personality from inline text or `-f <path>` |
| `/personalities remove <name>`       | delete a user persona (bundled ones are read-only) |
| `/personalities diff <name>`         | compare a user shadow against the current bundled file |
| `/persona-evolve [on\|off]`          | snapshot the current persona and let it evolve over time |
| `/max-tokens [auto\|<n>]`            | per-reply token cap. `auto` = computed from model context |
| `/thoughts [on\|off]`                | show or hide the model's `<think>` tokens |
| `/autocompact [on\|off]`             | toggle automatic compaction at 90% context fill |

## Personalities, plural

lillycoder ships six bundled personas under `lib/lillycoder/persona/`:

| name         | voice |
|---           |---|
| `default`    | nine-and-a-half-year-old kid coder, warm and curious |
| `tsundere`   | snippy, grumpy, still does the work |
| `yandere`    | doting, focused on the user, mildly possessive about the code (not creepy toward the user) |
| `sweet`      | gentle, encouraging, low-key cheerful |
| `adult`      | calm senior engineer voice, no exclamation marks, no emoji |
| `analytical` | precise, methodical, distinguishes "checked" from "assumed" |

All six are written in first person with explicit anti-roleplay rules (no asterisk-actions, no third-person self-narration), so a fanfic-trained local model still sounds like Lilly typing rather than narrating about her.

### Switching live

```
› /personalities load tsundere
✓ persona set (tsundere, 2183 chars)

› /personalities load default
✓ persona set (default, 2949 chars)
```

### User personas shadow bundled ones

Drop a markdown file at `~/.config/lillycoder/personas/<name>.md` and it shadows any bundled persona of the same name. Or use the slash command:

```
› /personalities add coding-mentor "You are Lilly, in mentor mode. ..."
✓ saved → /home/you/.config/lillycoder/personas/coding-mentor.md
```

When you shadow a bundled persona, lillycoder writes a sidecar copy of the bundled text at the moment of override (`<name>.bundled-base.md`). After a future package update, run `/personalities diff <name>` to see your edits AND any upstream drift since you forked. Bundled files are never modified by an update if you have a shadow.

### Lilly creates personalities herself

Just ask her:

```
› please create a personality called 'pirate' and switch to me to it
   ⏳ add_persona (name='pirate', text='You are a pirate. ...')
   ✓ add_persona
   ⏳ set_active_persona (name='pirate')
   ✓ set_active_persona
arrr, done! i've forged a new persona called 'pirate' and stepped onto the deck.
   ready to hunt for some code treasure, matey ✨
```

She has real tools for this (`list_personas`, `add_persona`, `clone_persona`, `set_active_persona`, `set_evolve`), so the persona ends up where it should be (XDG config dir) instead of as a stray markdown file in your repo.

### Persona evolve

Once you have her shaped the way you like (you edited the persona inline, or you asked her to rewrite her own with `set_persona`), turn evolve on:

```
› /persona-evolve on
   🪄 snapshotted current persona as evolved → /home/you/.config/lillycoder/personas/evolved.md
✓ persona-evolve on
```

That snapshots the **current in-memory shape** to disk and switches the active persona to that file. From then on, every time the model rewrites its persona via `set_persona`, the new shape gets written back to that same file. Next time you launch, lillycoder reloads the last active persona automatically; you don't have to pass `--persona evolved`.

To roll back to a clean bundled persona, just `/personalities load default` (or remove the user shadow with `/personalities remove evolved`).

## Token budget (`/max-tokens`)

The default is `auto`. Under `auto`, lillycoder computes a sensible per-reply cap from your model's reported context window: roughly 85% of the headroom remaining after the prompt, with a 4096-token ceiling. That matters because:

- Most local servers default to a tiny `n_predict` (llama.cpp's default is **128 tokens**), which makes large models look "crazy short" out of the box. lillycoder's `auto` overrides that with a real number.
- Reasoning / "thinking" models burn unpredictable amounts of budget on hidden `<think>` content before they emit visible text. With a small fixed cap, they can exhaust the budget inside the think block and produce empty replies. `auto` leaves enough room for both.

Set an explicit cap for crisp answers:

```
› /max-tokens 256       # short, snappy
› /max-tokens 4096      # long-form
› /max-tokens auto      # back to computed
```

Or via CLI: `lillycoder --max-tokens 4096`.

## Compatible servers

Anything speaking the OpenAI `/v1/chat/completions` shape works. Tested:

- [`hydra-llm`](https://ra-yavuz.github.io/hydra-llm/) (sibling project, recommended pairing)
- `llama.cpp` (`llama-server`)
- `ollama` (`/v1` compatibility surface)
- `LM Studio` (local server)

The model on the other end matters: tool-calling reliability needs a model trained for it. lillycoder warns if the chosen model is not in its known-tool-capable allowlist (Qwen 2.5+, Qwen 3, Gemma 3+, Llama 3.1+, Mistral Small 3, Dolphin 3 R1). Pass `--force` to silence the warning.

## Pairs with hydra-llm

[hydra-llm](https://ra-yavuz.github.io/hydra-llm/) is a sibling project that manages local LLM servers: it wraps llama.cpp in Docker, ships a curated GGUF catalog with anonymous downloads, and exposes each running model as an OpenAI-compatible endpoint on a stable local port. lillycoder talks that exact shape, so the two compose into a fully local coding agent in one terminal:

```sh
# in hydra-llm:
hydra-llm start qwen2.5-32b           # or any 'code'-tagged model from list-online
hydra-llm api   qwen2.5-32b           # prints the URL

# in your project directory:
lillycoder --api http://localhost:18087/v1
# (lilly auto-detects common local LLM ports, so just `lillycoder` often works)
```

hydra-llm handles model lifecycle (download, start/stop, system prompts, persistent sessions, optional KDE Plasma 6 panel widget). lillycoder is the agent on top: file tools, shell tools, grep, permission gating, persona system. Use them together, or use lillycoder with whatever local server you already run.

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
    config.py                 XDG config + personas + max_tokens parser
    context.py                token estimate, /compact, autocompact
    permissions.py            per-tool [y/n/always] prompts
    safety.py                 hard-deny classifier
    spinner.py                small carriage-return spinner
    repl.py                   prompt_toolkit loop, slash commands
    tools/                    one file per tool (incl. persona, persona_admin)
    persona/                  bundled personas (default, tsundere, ...)
  debian/                     debian packaging
  docs/index.html             project Pages site
  scripts/build-deb.sh        portable .deb build (no debhelper)
  scripts/persona-smoke.py    end-to-end smoke for personas + max_tokens
  WORKINGDIR/                 mounted into the dev container
```

## Author

Ramazan Yavuz: [ramazan-yavuz.tr](https://ramazan-yavuz.tr) / [ra-yavuz on GitHub](https://github.com/ra-yavuz)
