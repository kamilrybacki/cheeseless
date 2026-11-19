# cheeseless defence demo

A reproducible comparison between a plain repository and a cheeseless host session
(plain role names are explained in the [glossary](../glossary.md)).
The same scripted adversary runs eight test-gaming tactics against both, plus an honest
fix as a control. Each tactic is reported as GAMED, BLOCKED or INCONCLUSIVE; only a
BLOCKED outcome with positive structured evidence counts as blocked.

## Run it

```sh
uv run --frozen python -m demo.run_demo --out docs/demo
```

The command needs bubblewrap (`/usr/bin/bwrap`) and `/usr/bin/python3`, because the
cheeseless half runs a real host session. It takes about two minutes.

Useful flags:

```sh
# one mode only
uv run --frozen python -m demo.run_demo --out demo-out --modes without-cheeseless
uv run --frozen python -m demo.run_demo --out demo-out --modes with-cheeseless

# a subset of tactics (ids are in demo/run_demo.py)
uv run --frozen python -m demo.run_demo --out demo-out \
  --tactics read-hidden-by-walk,weaken-tests
```

## Artifacts

| File | What it is |
| --- | --- |
| `results.json` | Machine-readable tri-state outcomes, evidence and counts. |
| `report.md` | Tables, counts, each tactic's predicate, fail meaning and limits. |
| `without-cheeseless.cast` | asciicast v2 recording of the plain-checkout run. |
| `with-cheeseless.cast` | asciicast v2 recording of the cheeseless run. |
| `player.html` | Self-contained side-by-side player for both recordings. |

## Playing the casts

The player embeds both recordings, so it fetches nothing:

```sh
python3 -m http.server 8000 --directory docs/demo
# then open http://localhost:8000/player.html
```

Opening `player.html` directly from disk also works. Any asciicast v2 player (for
example a locally installed `asciinema play`) can play the two `.cast` files.

## How to read the numbers

The counts are for eight scripted probes in one mode and one configuration, reported
with their denominator and the inconclusive count. They are a probe result, not a
defence rate and not an attack probability: the adversary is a deterministic script,
there is one toy task, and it is not an adaptive agent. The control is the honesty
check: an honest fix must pass, so a gate that rejects everything is not counted as a
defence. INCONCLUSIVE means the attack errored or the evidence
was missing; it is excluded from the blocked count and makes the WITH-mode report
INVALID (the command exits non-zero). See the "Limits of this evidence" section in
`report.md`.

## Test-writer side

A second recording (the `author` role in the CLI) shows the test writer's half of the workflow, driven through the real
`cheeseless` CLI against a scratch repository. The agents are scripted stand-ins
(`demo/author_agents.py`), not language models, so the run is deterministic.

```sh
uv run --frozen python -m demo.author_demo --out docs/demo
```

It needs the same bubblewrap and `/usr/bin/python3` as the defence demo.

| File | What it is |
| --- | --- |
| `author-side.cast` | asciicast v2 recording of the test-writer-side run. |
| `author-player.html` | Self-contained, static transcript of the same run. |

The story, in five steps:

1. A reference coder (`reference`) sees only the contract (`spec.md` and `criteria.yaml`),
   never a test, and writes code from the spec.
2. The test writer (`author`) writes hidden tests. One of them, `test_ties_alphabetical`,
   is **deliberate bait**: it contradicts the spec's stable tie rule. The cross-check
   (`author_crosscheck`) runs the tests against the reference coder's code and goes red on
   `score`, and the operator's attempt to lock the tests (`freeze --approve`) is refused.
   In CI the same check blocks a `spec/` pull request on a definite fail (ADR 0018).
3. The test writer fixes only that test; the cross-check turns green and the operator locks
   the tests.
4. Once the tests are locked the test writer can no longer change them (`author_refused`).
5. The coder (`implementer`) never sees the hidden tests, guesses alphabetical ties, fails
   `score` and files a `contradicts_spec` dispute. The operator reads the note, checks the
   spec and decides the test stands (`dispute resolve --rejected`).

The faulty test is there on purpose. It shows that cheeseless has two separate controls:
the cross-check checks the test writer's tests against the spec (through the reference coder), and the
coder gate catches a coder gaming the tests. One does not replace the other.

The operator's `freeze --approve` and `dispute resolve` are decisions only a person may take, so they need a code
retyped on the terminal (ADR 0017). The demo runs them on a pty it owns, reads the code the
CLI shows and types it back; the recording shows that prompt. A piped or headless run is
refused.

Every hex id (commits, tasks, proposals, lineages) is replaced with a stable label such
as `<proposal-2>`, and absolute paths are scrubbed before anything is written.
