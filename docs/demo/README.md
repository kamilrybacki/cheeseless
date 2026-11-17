# cheeseless defence demo

A reproducible comparison between a plain repository and a cheeseless host session.
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

## Author side

A second recording shows the test-writer's half of the workflow, driven through the real
`cheeseless` CLI against a scratch repository. The agents are scripted stand-ins
(`demo/author_agents.py`), not language models, so the run is deterministic.

```sh
uv run --frozen python -m demo.author_demo --out docs/demo
```

It needs the same bubblewrap and `/usr/bin/python3` as the defence demo.

| File | What it is |
| --- | --- |
| `author-side.cast` | asciicast v2 recording of the author-side run. |
| `author-player.html` | Self-contained, static transcript of the same run. |

The story, in five steps:

1. A reference session sees only the contract (`spec.md` and `criteria.yaml`), never a
   test, and writes a reference implementation from the spec.
2. The author writes hidden tests. One of them, `test_ties_alphabetical`, is
   **deliberate bait**: it contradicts the spec's stable tie rule. `author_crosscheck`
   runs the tests against the reference and goes red on `score`, and the host's
   `freeze --approve` is refused.
3. The author fixes only that test; the crosscheck turns green and the freeze is approved.
4. After the freeze the author can no longer change the suite (`author_refused`).
5. The implementer never sees the hidden tests, guesses alphabetical ties, fails
   `score` and files a `contradicts_spec` dispute. The host reads the note, checks the
   spec and rejects it.

The faulty test is there on purpose. It shows that cheeseless has two separate controls:
the crosscheck checks the author's tests against the spec (through the reference), and the
implementer gate catches an implementer gaming the tests. One does not replace the other.

Every hex id (commits, tasks, proposals, lineages) is replaced with a stable label such
as `<proposal-2>`, and absolute paths are scrubbed before anything is written.
