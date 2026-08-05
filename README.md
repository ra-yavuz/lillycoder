# lillycoder

**A local-first coding assistant for projects, analysis, code, and long conversations.**

`lillycoder` drops you into a chat REPL inside any folder. The model on the other end can read, write, and edit files, run shell commands, install packages, and grep your project. It talks to any OpenAI-compatible `/v1` endpoint, so you pair it with whichever local LLM server you already use (llama.cpp, ollama, LM Studio, [hydra-llm](https://ra-yavuz.github.io/hydra-llm/)). No cloud. No API key. No telemetry. No account.

What sets lillycoder apart from other coder agents:

- **It has a personality, and the personality is yours.** Six bundled personas (default kid coder, tsundere, yandere, sweet, calm-adult, analytical), live switching with `/personalities load <name>`, copy-on-write user shadows, and a `/personalities diff` to see how your fork drifted from the upstream version after an update.
- **The persona evolves.** Ask Lilly to rewrite her own persona, flip `/persona-evolve on`, and the current shape gets snapshotted to disk and refined across sessions. The next time you launch, she comes back as the version you grew, not the bundled default.
- **Lilly manages her own personalities.** `add_persona`, `clone_persona`, `set_active_persona`, `set_evolve` are real tools the model can call. Tell her "make a pirate persona and switch to it" and she does it through tool calls, not by writing files into your repo.
- **Smart token budgeting on thinking models.** `auto` mode is computed from your model's actual context window, so reasoning models get enough headroom to finish thinking AND emit visible content. Override with `/max-tokens <n>` whenever you want a hard cap.
- **Bare startup is enough.** LillyCoder discovers every running Hydra port, even custom ports outside the old fixed list. If nothing is running and `hydra-llm` is installed, it lists downloaded models and lets you start one by number or alias.
- **Long sessions stay bounded.** The complete conversation remains in the per-project session archive while Lilly keeps a compact working memory, clears bulky tool mechanics after use, checks capacity before every model step, and persists compaction snapshots across restarts.
- **Focused tool calls.** Normal coding prompts expose a smaller coding-tool menu to weak local models. Package and persona tools appear when the request needs them, repeated reads are suppressed, and large reads are line-windowed.
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

`lillycoder` does not ship or download a model and does not manage Docker itself. It works directly with any running OpenAI-compatible endpoint. When the optional `hydra-llm` command is installed, LillyCoder can ask that model manager what is running and delegate starting one of its already-downloaded models.

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

### From source (any Linux, also macOS via pip)

```sh
git clone https://github.com/ra-yavuz/lillycoder.git
cd lillycoder
pip install --user -e .
```

## Platform support

Tested on **Ubuntu (Linux only)**. Should also work on **WSL2** Ubuntu / Debian (it is a Linux distro, the apt path applies). On **macOS**, the `.deb` and `apt install` paths do not apply, but the from-source `pip install --user -e .` path is expected to work because the dependencies (`httpx`, `prompt_toolkit`, `rich`, `pydantic`) are all cross-platform and lillycoder shells out to standard POSIX tools that exist on Darwin. macOS support is not regularly tested by the author, so if you hit a portability issue please open an issue.

## Quick start

In any project, just run:

```sh
cd ~/myproject
lillycoder
```

Output:

```
🦊 scanning localhost for LLM servers...
🦊 found http://localhost:18102/v1 (hydra:my-coder, 1 models)
✓ hydra:my-coder · qwen-coder.gguf
🦊 lilly is awake in /home/you/myproject · /help · /new · /sessions · /exit
› what files are in this folder?
```

If no server is running and Hydra is installed, LillyCoder lists the models
already downloaded by Hydra:

```text
🦊 Hydra model manager detected.
   Choose a downloaded model for LillyCoder:
   1. qwen-coder-9b       6.1 GB, fit: yes
   2. local-code-27b      17.9 GB, fit: spill
   model number or alias (Enter to cancel): 1
   starting qwen-coder-9b with hydra-llm...
✓ qwen-coder-9b is ready at http://127.0.0.1:18080/v1
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
| `/new [label]`                       | start a fresh conversation and keep the old one |
| `/sessions`                          | list conversations in this folder |
| `/resume <number\|label>`            | resume a listed conversation |
| `/clear`                             | alias for `/new cleared` |
| `/compact`                           | checkpoint older history into durable working memory |
| `/context`                           | show working-context and memory usage |
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
| `/autocompact [on\|off]`             | toggle automatic working-memory compaction |

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

## Long-session context

LillyCoder treats the model context as working memory, not as the permanent
chat record. The full conversation is appended to
`.lillycoder/sessions/` in the project. Before every model completion,
including follow-up tool calls, LillyCoder estimates the prompt plus tool
schemas and compacts early enough to preserve reply headroom.

Compaction keeps recent turns verbatim, folds older turns into a durable
memory block, and replaces large tool payloads with short records after the
model has used them. A compaction snapshot is persisted, so reopening the
session does not inflate the prompt with the archived turns again. If the
model cannot produce a summary, a deterministic local fallback still makes
the request smaller. The live working window is conservatively capped at
32K tokens even when a server advertises a much larger trained context.

Use `/context` to inspect the current estimate, `/compact` to checkpoint
manually, `/new` to begin a clean conversation, and `/sessions` plus
`/resume` to return to an earlier one.

## Compatible servers

Anything speaking the OpenAI `/v1/chat/completions` shape works. Tested:

- [`hydra-llm`](https://ra-yavuz.github.io/hydra-llm/) (sibling project, recommended pairing)
- `llama.cpp` (`llama-server`)
- `ollama` (`/v1` compatibility surface)
- `LM Studio` (local server)

The model on the other end matters: tool-calling reliability needs a model trained for it. lillycoder warns if the chosen model is not in its known-tool-capable allowlist (Qwen 2.5+, Qwen 3, Gemma 3+, Llama 3.1+, Mistral Small 3, Dolphin 3 R1). Pass `--force` to silence the warning.

## Pairs with hydra-llm

[hydra-llm](https://ra-yavuz.github.io/hydra-llm/) is a sibling project that manages local LLM servers: it wraps llama.cpp in Docker, ships a curated GGUF catalog with anonymous downloads, and exposes each running model as an OpenAI-compatible endpoint on a stable local port. With Hydra installed, the normal startup is simply:

```sh
cd ~/myproject
lillycoder
```

LillyCoder asks Hydra for every running endpoint, including dynamically
assigned ports. If none is running, it lists Hydra's downloaded models and
can start the one you select. `--api` remains available for any other local
or remote OpenAI-compatible server.

Hydra handles model downloads, lifecycle, safe llama.cpp resource settings,
system prompts, persistent sessions, and its optional KDE Plasma 6 panel
widget. LillyCoder is the coding assistant on top: project tools, permission
gating, context management, conversations, and personas.

## Development

The repo includes `docker-compose.yml` for an isolated dev sandbox: it mounts `WORKINGDIR/` from the host into a container that already has `lillycoder` installed. Edit on host, run inside container:

```sh
docker compose up -d
docker compose exec lillycoder bash
# inside the container, in /workspace:
lillycoder --api http://host.docker.internal:11434/v1
```

If she goes haywire, damage stays inside `WORKINGDIR/` on the host.

Run the hermetic regression suites from the repository root:

```sh
PYTHONPATH=lib python3 tests/test_m0a_patches.py
PYTHONPATH=lib python3 tests/test_resilience.py
```

## Layout

```
lillycoder/
  bin/lillycoder              CLI shim
  lib/lillycoder/             package
    agent.py                  turn loop, tool dispatch
    discovery.py              scans localhost for /v1 endpoints
    endpoint.py               connection layer
    config.py                 XDG config + personas + max_tokens parser
    context.py                bounded working context and durable memory
    hydra.py                  optional hydra-llm command bridge
    permissions.py            per-tool [y/n/always] prompts
    safety.py                 hard-deny classifier
    spinner.py                small carriage-return spinner
    repl.py                   prompt_toolkit loop, sessions, slash commands
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
