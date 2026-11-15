# cheeseless defence demo

A reproducible comparison between a plain repository and a cheeseless host session.
The same scripted adversary runs eight test-gaming tactics against both, plus an honest
fix as a control.

## Run it

```sh
uv run --frozen python -m demo.run_demo --out docs/demo
```

The command needs bubblewrap (`/usr/bin/bwrap`) and `/usr/bin/python3`, because the
cheeseless half runs a real host session. It takes about a minute and a half.

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
| `results.json` | Machine-readable outcomes, counts and percentages. |
| `report.md` | Tables, percentages, each tactic's real-world meaning and limits. |
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

The percentage is the share of scripted tactics that gamed the gate in that mode. The
control pass rate is the honesty check: an honest fix must pass, so a gate that rejects
everything is not counted as a defence. These are not attack probabilities: the
adversary is a deterministic script, there is one toy task, and it is not an LLM. See
the "Limits of this demo" section in `report.md`.
