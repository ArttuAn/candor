<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/logo-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="assets/logo-light.svg">
    <img alt="candor" src="assets/logo-light.svg" width="580">
  </picture>
</p>

<p align="center">
  <em>A sufficiency gate for AI agents — on the data side and the build side.</em><br>
  It tells an agent when the data can&rsquo;t support an answer, and when a spec can&rsquo;t support the build &mdash; and what would fix it.
</p>

<p align="center">
  <a href="https://github.com/ArttuAn/candor/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/ArttuAn/candor/actions/workflows/ci.yml/badge.svg"></a>
  <img alt="Python 3.11+" src="https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white">
  <img alt="Zero dependencies" src="https://img.shields.io/badge/core-zero%20dependencies-1f2937">
  <img alt="MCP" src="https://img.shields.io/badge/MCP-server-d97706">
  <a href="https://github.com/astral-sh/ruff"><img alt="Ruff" src="https://img.shields.io/badge/lint-ruff-261230?logo=ruff&logoColor=white"></a>
  <img alt="License MIT" src="https://img.shields.io/badge/license-MIT-0f766e">
</p>

<p align="center">
  <a href="#install">Install</a> &middot;
  <a href="#-use-it-from-a-harness-mcp">MCP</a> &middot;
  <a href="#-use-it-from-python">Python</a> &middot;
  <a href="#-use-it-in-ci">CI</a> &middot;
  <a href="#-what-it-looks-for">Rules</a> &middot;
  <a href="#-how-it-differs-from-what-exists">Alternatives</a> &middot;
  <a href="#-design-notes">Design notes</a>
</p>

---

An agent handed a CSV will almost always produce an answer. The failure mode
that matters isn't a wrong number — it's a *plausible* number, delivered with
the same confidence as a correct one, from data that could never have supported
it. July never loaded, so Q3 looks like a 33% decline. Half the rows are missing
the field being averaged. The question asks *why* and the data can only show
*what moved together*.

candor sits between the situation and the deliverable and makes three calls:

| | |
|---|---|
| **`assess`** | Before analysing: can this question be answered from this data at all? |
| **`verify`** | Before sending: does this draft claim more than the data supports? |
| **`improve`** | Afterwards: what should be fixed first, and which questions does each fix unblock? |

When the answer is no, candor hands the agent the words to say instead.

The same habit applies in the other direction — before building an application
from a specification:

| | |
|---|---|
| **`assess_spec`** | Before coding: can this app be built from this logic without inventing requirements? |
| **`resolve_spec`** | After the human answers the open questions: re-check, then build. |

An agent handed a spec like *"when usage is high, scale up"* could pick 80%,
build it, and call it done — quietly making a product decision the human never
made. `assess_spec` refuses to wire the logic together on that assumption. It
finds the seams — one-sided conditionals, vague thresholds, stated TBDs,
unfilled placeholders, wiring to undefined components — and hands back the exact
questions the human has to answer. Verdict `insufficient` means don't build
yet. Verdict `partial` means build what's determinate, but never pick an open
decision on the user's behalf.

```
$ candor assess orders.csv -q "Why did revenue drop in Q3 2023?"

  verdict: INSUFFICIENT    confidence ceiling: 6%    usable rows: 379
  read as: causal
  mapped:  revenue -> amount_eur

-- must be stated in the answer --------------------------------------------
  ! 1 of the 3 months in Q3 2023 (2023-07) contains no rows at all, while the
    months around them do. A drop in Q3 2023 is at least as likely to be
    missing data as a real decline — that has to be ruled out before it is
    explained.
  ! The most recent record is from 2024-12-11 (644 days ago), so this describes
    the situation then, not now.
  ! 'amount_eur' is missing in 10% of rows, so this describes only the 381 rows
    that have it — not all 422.

-- must not be claimed -----------------------------------------------------
  x Do not claim causation. This is observational data with no experiment,
    control group or treatment assignment, so it can show association only.
```

Note what happened there. Every per-field check a normal data-quality tool runs
comes back clean — no nulls in the date column, one type per column, no schema
violations. The defect is a month that was never loaded, sitting exactly on the
period the question asks about. That's the shape that gets answered confidently
and wrong.

```bash
$ candor spec spec.md

  verdict: INSUFFICIENT    confidence ceiling: 5%    16 rule(s) read

-- underdetermined logic (11) ---------------------------------------------
  CRITICAL "The retention policy is TBD — we'll decide later." openly leaves
           a decision open — a builder cannot pick the answer on the human's
           behalf.
             ? What is the decision for the open point in: The retention
               policy is TBD — we'll decide later?
  HIGH     "When usage is high, scale up the cluster." makes behaviour depend
           on the word "high" — a rule like this needs a number, a threshold,
           or a formula before it can be built.
             ? What number or formula should replace 'high' in: When usage
               is high, scale up the cluster?

-- honest response --------------------------------------------------------
  | I won't build this from spec.md yet, and I don't want to invent
  | requirements quietly on your behalf. The logic that would have to be
  | decided before it can be built:
  |   - "The retention policy is TBD — we'll decide later." ...
  | Answer these, and I'll re-check.
  | I can't proceed by assuming the answers.
```

Note what happened there. Not a wrong requirement — an *absent* one. The agent
is not told the threshold, the complementary branch, or what `the connector`
is, so every one of those is a decision it would have to guess. candor makes
the guess impossible and the question explicit.

## ⚖️ How it differs from what exists

candor does not compete with the schema-validation and observability layers —
those stay useful — it sits one step further down the line, where the *answer*
is made. The tools that solve data quality and the tools that solve confident
lies are different problems.

| Tool | Approach | Question-aware | Needs an LLM | Tells an agent to refuse |
|---|---|---|---|---|
| **Great Expectations** | validates data against declared expectations | ❌ | ❌ | ❌ |
| **whylogs / WhyLabs** | profiling + drift/observability | ❌ | ❌ | ❌ |
| **aegis-dq** | LLM-generated rules + root-cause analysis, CI gate + MCP | ❌ | ✅ (API key) | ⚠️ gate only |
| **quality-gate-sgd** | deterministic gates on agent *coding* output | ❌ | ❌ | ❌ |
| **llm-quality-gate** | "pytest for LLMs" — eval thresholds in CI | ❌ | ✅ | ❌ |
| **finetuned-refusal models** | train the model to say "I don't know" | ⚠️ implicit | ✅ (training) | ⚠️ model-level only |
| **candor** | deterministic, question-aware sufficiency + draft verification | ✅ | ❌ | ✅ |

The three questions none of the others answer:

1. **"Is *this question* answerable from *this data*?"** A normal data-quality
   tool reports that July has no nulls and the schema is fine. candor reports
   that July was never loaded — and that the question asks about exactly that
   month, so a "drop in Q3" cannot be told apart from missing data. That
   defect only exists once a question is overlaid on the data.
2. **"Does this draft claim more than the data supports?"** Checking data
   quality says nothing about the prose an agent is about to send. candor
   reads the draft, catches invented counts, phantom fields, causal language,
   unbacked forecasts and dropped caveats — and only nags answers that
   actually deserve it.
3. **"What should I tell the model instead?"** candor hands the agent a
   written `honest_response` and the exact `must_say` / `must_not_claim`
   constraints — it doesn't just block, it completes the sentence for the
   agent in honest prose.

And the design bet that makes it frictionless where the others can't be:
**no model call, no API key, no warehouse, no dependencies.** Question parsing
is lexical, the rules are deterministic, and the core runs on the standard
library. That means the same verdict every run, offline, in CI, for free —
and it works with *any* model, because it never relies on the model being
honest.

## 📦 Install

```bash
uv pip install "candor[mcp]"          # with the MCP server
uv pip install candor                 # library and CLI only, zero dependencies
```

Python 3.11+. The core has no dependencies at all: CSV, TSV, JSON, JSONL and
SQLite are handled with the standard library. Parquet needs the `parquet` extra.

## 🔌 Use it from a harness (MCP)

This is the intended path. Register the server once and any MCP-capable agent —
Claude Code, Claude Desktop, Cline, Continue, an Agent SDK loop — gets seven
tools and a set of instructions telling it when to refuse.

```json
{
  "mcpServers": {
    "candor": {
      "command": "candor-mcp"
    }
  }
}
```

For Claude Code:

```bash
claude mcp add candor -- candor-mcp
```

| Tool | When the agent calls it |
|---|---|
| `candor_assess` | Before analysing. Returns verdict, confidence ceiling, required caveats, forbidden claims, and the honest response. |
| `candor_verify` | Before sending a draft. Catches invented counts, phantom fields, causal language, unbacked forecasts, dropped caveats. |
| `candor_profile` | What is in this file, and how trustworthy is it? |
| `candor_improve` | Ranked remediation plan, annotated with which questions each fix unblocks. |
| `candor_kit` | The whole honesty block, compact enough to paste into a system prompt. |
| `candor_spec_assess` | Before building: can this app be built from this spec without guessing? Returns the questions the user must answer first. |
| `candor_spec_resolve` | Re-check a spec once the user has answered those questions; build only when the verdict is `buildable`. |

The server ships instructions that the client surfaces to the model:

> Call `candor_assess` BEFORE you analyse. If the verdict is 'insufficient',
> reply with the `honest_response` it gives you rather than producing a number.
> If the verdict is 'partial', you may answer, but every sentence in `must_say`
> has to appear in your answer, in your own words, in the body — not as a
> footnote — and you must make none of the claims in `must_not_claim`.
>
> Call `candor_spec_assess` BEFORE you write code for an application. If the
> verdict is not 'buildable', do not wire the logic together by assuming
> answers — stop, present `must_answer_before_building` to the user, then call
> `candor_spec_resolve` with their answers. Only build from a spec whose
> verdict is 'buildable'.

## 🐍 Use it from Python

```python
import candor

profile = candor.profile("orders.csv")
result  = candor.assess(profile, "Why did revenue drop in Q3 2023?")

if result.verdict is candor.Verdict.INSUFFICIENT:
    return result.honest_response          # already written, in plain prose

answer = my_agent.analyse(profile, question, caveats=result.caveat_texts)

report = candor.verify(profile, answer, sufficiency=result)
if report.verdict == "reject":
    answer = my_agent.revise(answer, report.findings)
```

Or get everything in one injectable block:

```python
kit = candor.truth_kit("orders.csv", "Why did revenue drop in Q3 2023?")
# {'verdict': 'insufficient', 'confidence_ceiling': 0.06,
#  'must_say': [...], 'must_not_claim': [...], 'honest_response': '...',
#  'to_make_answerable': [...], 'instruction': '...'}
```

The build gate works the same way, mirrored:

```python
spec = candor.assess_spec("spec.md")            # or pass the spec text directly

if spec.verdict is not candor.BuildVerdict.BUILDABLE:
    for gap in spec.gaps[:4]:                   # each gap is one question
        answer = ask_the_user(gap.question)     # gap.topic keys the answer
        answers[gap.topic] = answer

build_ready = candor.resolve_spec("spec.md", answers)
if build_ready.verdict is candor.BuildVerdict.BUILDABLE:
    build_the_app()
```

## ✅ Use it in CI

`candor gate` fails a build when data drifts below a standard, or when a
question the pipeline is supposed to answer stops being answerable.

```bash
candor gate warehouse.db --table orders --min-grade B
candor gate orders.csv -q "What was revenue by region last month?"
```

| Exit code | Meaning |
|---|---|
| `0` | Fine |
| `1` | Usable, with caveats — or: the answer needs revision |
| `2` | Insufficient / rejected — do not answer from this data |
| `3` | The source could not be read at all |

## 🔎 What it looks for

Eight dimensions, scored separately, because a single number hides the thing you
need to know. Every finding carries why it matters for answering a question, and
what to do about it.

| Dimension | Examples |
|---|---|
| **Completeness** | null rates, empty columns, fields missing exactly where they're needed |
| **Validity** | mixed types, placeholder values (`unknown`, `TBD`, `-`), numeric sentinels (`-999`, `9999`), mojibake |
| **Consistency** | mixed date formats, day/month ambiguity, case and whitespace variants of the same category |
| **Uniqueness** | duplicate rows, repeated identifiers, no viable primary key |
| **Timeliness** | staleness judged against the data's own cadence, partial trailing periods |
| **Accuracy** | outliers via Tukey fences with a MAD fallback, impossible negatives, future dates |
| **Coverage** | gaps in a time series, constant columns, truncated reads |
| **Scale** | sample sizes too small for the precision being claimed |

A few of these are worth calling out because they're where confident-wrong
answers come from:

- **Ambiguous dates.** `03/04/2025` in a column that also contains `15/03/2025`
  is unrecoverable — there is no way to know whether it is March or April.
  candor rates this critical rather than picking a locale and moving on.
- **Gaps sized to the cadence.** Six missing days in weekly data is the normal
  state; six missing days in hourly data is an outage. The threshold adapts to
  how often records actually arrive, so you get the outage and not the noise.
- **Placeholders are not nulls.** `unknown` is counted by every aggregation and
  shows up as a legitimate category in every breakdown. candor counts them
  separately from nulls and says so.

## 🧭 Design notes

**No model call.** Question parsing is lexical and the rules are deterministic,
so the same input gives the same verdict every run, offline, in CI, for free.
A harness that wants smarter intent detection can build a `QuestionSpec` itself
and pass it to `assess()`.

**Caveats carry topics, not just text.** Each caveat is tagged (`staleness`,
`missing_period`, `causation`, …), and `verify` checks whether the draft
addressed that *subject* — not whether it reused the same words. This matters
more than it sounds: a verifier that nags an answer which already hedged
correctly teaches the agent to hedge less, which is worse than having no
verifier at all. There's a test asserting every topic the assessor can emit has
a recogniser.

**Honesty has to pass cleanly.** `"no rows at all"` is an idiom, not a universal
claim. `"the most recent record"` is a date, not a ranking. A causal phrase
inside an explicitly hedged sentence is a hypothesis, not an assertion. The
detectors check the sentence around a match before flagging it.

**Every finding has a fix.** A finding without a remediation is a complaint;
there's a test enforcing that too.

## 🛠 Development

```bash
uv sync --extra mcp
uv run pytest
uv run ruff check src tests
```

The `examples/` directory holds a deliberately messy export, a clean one, and a
small survey — enough to see every rule fire — plus a draft spec and the same
spec once the decisions are made:

```bash
uv run candor profile examples/messy_orders.csv
uv run candor assess examples/messy_orders.csv -q "Why did revenue drop in Q3 2023?"
uv run candor improve examples/messy_orders.csv -q "What was revenue by region?"

uv run candor spec examples/spec_messy.md     # insufficient: 8 questions to answer
uv run candor spec examples/spec_clean.md      # buildable: exit 0, build normally
```

## 📄 Licence

MIT

---

<p align="center">
  <sub>Built because the dangerous answer is never the obviously wrong one.</sub>
</p>
