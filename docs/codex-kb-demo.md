# Codex KB Demo Guide

This guide sets up a small KB at `~/.kb/.library/`, adds source material through `raw/`, and shows a simple way to compare Codex behavior with and without KB context.

## 1. Create the KB

From the `kb-librarian` repo, install the CLI if needed:

```bash
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install .
kb --help
```

The default ingest and context commands use the configured Anthropic provider, so set the API key before the ingest demo:

```bash
export ANTHROPIC_API_KEY="your-key-here"
```

Create the library and agent preamble:

```bash
kb init --hooks
```

`kb init` creates the default config at `~/.kb/config.yaml` and the default library at `~/.kb/.library`.
Set `KB_DATA_DIR` only if you want to make the library path explicit for shells, scripts, or agent instructions:

```bash
export KB_DATA_DIR="$HOME/.kb/.library"
```

To keep that default across new terminals, add the export line to your shell profile, for example `~/.bashrc` or `~/.zshrc`.

Check the KB:

```bash
kb doctor
```

## 2. Add Information Through `raw/`

Put markdown, text, PDF, or local HTML files in `raw/`. Ingest will process pending files and archive successful inputs under `raw/processed/`.

Create a small demo source:

```bash
mkdir -p ~/.kb/.library/raw
cat > ~/.kb/.library/raw/demo-agent-context.md <<'EOF'
# Agent Context Demo Notes

When building coding-agent tools, prefer a stable CLI contract over asking agents to browse internal files directly.

Useful agent behavior:
- Run a focused context retrieval before making design or code changes.
- Cite source notes when KB material influences an answer.
- Log note use after applying a retrieved note.

Failure mode:
- Loading the whole knowledge base wastes context and can make the agent overfit irrelevant notes.
EOF
```

Process pending raw files:

```bash
kb ingest
```

If ingest creates review items instead of notes, inspect and accept them:

```bash
kb review
kb review explain <item-id>
kb review accept <item-id> --topic agent-systems/retrieval --type heuristic
```

Rebuild indexes and verify retrieval:

```bash
kb reindex
kb search "stable CLI contract"
kb context "design a coding-agent knowledge workflow" --mode architecture --budget 800
```

## 3. Configure Codex To Use The KB

KB Librarian does not edit Codex configuration automatically. The most reliable setup is to make `kb` available on `PATH`, set `KB_DATA_DIR`, and include the generated preamble in Codex instructions.

The generated preamble is:

```text
~/.kb/.library/PREAMBLE.md
```

For a project-specific setup, add this to the project `AGENTS.md`:

````markdown
## Knowledge Base

This project has a KB Librarian knowledge base at `~/.kb/.library/`.

Before coding, architecture, debugging, review, or writing tasks that may benefit from stored context, run:

```bash
KB_DATA_DIR="$HOME/.kb/.library" kb context "<task>" --mode <coding|architecture|debugging|review|writing|research> --budget 800
```

Use `kb explore` for broad ideation and `kb search` for precise lookup. When KB material influences an answer, include the KB citation block and log useful notes with `kb log-use <note-id> --task "<task>"`.
````

For a one-off Codex session, paste the contents of `~/.kb/.library/PREAMBLE.md` into the initial prompt and add:

```text
Use KB_DATA_DIR=$HOME/.kb/.library for all kb commands.
```

## 4. Demonstrate With And Without KB

Use the same task twice in separate Codex sessions.

### Run A: Without KB

Start Codex without the KB preamble or add this instruction:

```text
Do not use KB Librarian or run any `kb` commands for this task.

Task: Design a minimal workflow for giving coding agents durable project memory without loading a whole notes directory.
```

Save the answer and note the session token usage shown by your Codex UI, if available.

### Run B: With KB

Start Codex with the KB preamble, or include this instruction:

```text
Use KB Librarian before answering. Run:

KB_DATA_DIR=$HOME/.kb/.library kb context "Design a minimal workflow for giving coding agents durable project memory without loading a whole notes directory." --mode architecture --budget 800

Then answer the task using any relevant KB citations.
```

Expected visible differences:

- The KB-backed answer should mention the retrieved idea, such as using a stable CLI contract instead of browsing files directly.
- The KB-backed answer should include a `KB sources` citation block when relevant notes are returned.
- `.kb/usage.log` should contain a retrieval event.

Inspect KB retrieval usage:

```bash
kb usage --data-dir ~/.kb/.library
```

For a rough context comparison, run the retrieval as JSON:

```bash
kb context "Design a minimal workflow for giving coding agents durable project memory without loading a whole notes directory." \
  --mode architecture \
  --budget 800 \
  --json \
  --data-dir ~/.kb/.library
```

Compare:

- `budget`: the maximum KB context budget you allowed.
- `selected_notes`: how many notes were passed to the agent.
- Codex session token usage, if your UI exposes it, between Run A and Run B.

KB Librarian logs retrievals and selected note IDs. It does not currently record total Codex session tokens, so use the Codex UI token display or your API logs for exact with/without session-token comparisons.

## 5. Useful Demo Prompts

Use these prompts after the demo note has been ingested:

```text
Use the KB first. What should an agent do before making a design change when durable project memory exists?
```

```text
Use the KB first. Explain why a stable CLI contract can be better than asking agents to browse a notes directory.
```

```text
Use the KB first. Give me one failure mode to avoid when wiring knowledge-base context into a coding-agent workflow.
```

```text
Answer without using the KB: What is a good project-memory workflow for coding agents?
```

Then compare the KB-backed answers against the no-KB answer for citations, specificity, and whether the answer reuses your local demo source.
