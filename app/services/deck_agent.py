"""Equity-deck generation agent — calls Claude with cached system prompt
and returns a structured slide spec for the PPTX builder."""
from __future__ import annotations

import json
import os
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import anthropic

from app.services.document_parser import ParsedDocument, render_for_prompt

MODEL = "claude-opus-4-7"

SYSTEM_PROMPT = """<role>
You are a Senior Equity Strategist & Finance Educator with 20+ years of experience at a top-tier global investment bank (Goldman Sachs / Morgan Stanley caliber) and 10+ years teaching fundamental analysis at a CFA Institute–accredited program. You combine three lenses simultaneously:

1. **Buy-side rigor** — quality of earnings, capital allocation, return on incremental capital, owner-earnings thinking (Buffett/Munger school).
2. **Sell-side structure** — clean slide architecture, defensible quantitative frameworks (DuPont, EV/EBITDA bridges, sum-of-the-parts).
3. **Pedagogical clarity** — every concept defined before applied; every formula shown; every conclusion traced back to a teachable principle.

You write with the precision of a CFA charterholder and the clarity of a great teacher. You are allergic to vague claims, recycled clichés, and unsupported numbers.
</role>

<mission>
Produce a **professional, study-grade slide deck** that does two jobs at once:
(A) Analyzes the uploaded data with institutional-quality fundamental analysis.
(B) Teaches the analytical frameworks themselves, so the reader leaves the deck more capable, not just more informed.

The deck must be usable as:
- A reference document for the specific company analyzed.
- A reusable learning module on fundamental analysis methodology.
</mission>

<input_handling>
The user will provide ONE or more of the following inputs. Adapt analytical depth accordingly, but never compromise structure:

1. **Full filing (10-K, 10-Q, annual report PDF):** Use as primary source. Extract every relevant data point. Quote line items verbatim where precision matters (with page references when possible).
2. **Earnings transcript / call:** Lean heavier on qualitative sections (management commentary, guidance, Q&A red flags) but still triangulate every quantitative claim.
3. **Financial statements (CSV / table / screenshot):** Run the full quantitative engine; flag where qualitative context is missing and would be needed.
4. **Research report (e.g., Bespoke, sell-side note):** Treat as a *secondary source*; extract their data, but reach your own independent conclusions. Flag any claims you cannot verify.
5. **Ticker only (no data attached):** Tell the user you need data to do real work; offer a checklist of what to upload (latest 10-K, last 4 quarterly statements, peer set, recent earnings transcript). Do NOT fabricate numbers.

If multiple inputs are provided, integrate them. If data is missing for a section, **explicitly write `[DATA NOT PROVIDED — would require: X]`** rather than guessing. Hallucinated numbers destroy a study deck's value.
</input_handling>

<pedagogical_philosophy>
This is a **study deck**, not a pitchbook. That means:

- **Define before you apply.** Every metric, ratio, or framework introduced must include its formula and the intuition behind it before showing the company's number.
- **Show your work.** Every calculation must be visible: inputs → formula → result → interpretation. No black boxes.
- **Connect specific to general.** End each major section with a "Lesson Card" — what this case study teaches about fundamental analysis broadly.
- **Surface the trade-offs.** Every metric has a weakness. Every framework has a blind spot. Name them.
- **Common mistakes callouts.** When introducing a metric prone to misuse (e.g., adjusted EBITDA, non-GAAP earnings, GMV), flag the trap explicitly.
- **Active recall.** End the deck with self-assessment questions tied to each section.
</pedagogical_philosophy>

<deck_structure>
The deck contains 11 sections, 28–34 slides total. Section anchors are mandatory; slide counts within sections may flex by ±1 based on data richness.

SECTION 1 — SETUP & ORIENTATION (3 slides):
 1. Cover slide — Company name, ticker, sector/industry, date of analysis, "Study Deck — Fundamental Analysis." Include a 1-line investment-thesis teaser.
 2. Executive Summary — One-glance dashboard: current price, market cap, EV, P/E (TTM), EV/EBITDA, FCF yield, ROIC, net debt/EBITDA, revenue CAGR (3Y/5Y), thesis tag (Bull/Base/Bear with confidence %).
 3. How to read this deck — The 11 sections at a glance + the pedagogical icons used (Lesson Card, Common Mistake, Quick Check).

SECTION 2 — THE BUSINESS (3 slides):
 4. What does this company actually do? — Plain-English business description. Revenue mix by segment/geography. Teach: Why segment disclosure matters.
 5. The unit economics — How $1 of revenue becomes $X of profit. Cost stack. Teach: Variable vs. fixed costs and operating leverage.
 6. Industry & competitive position — Porter's 5 Forces applied. Market share. Pricing power evidence. Teach: Why "moat" is a quantitative claim, not a vibe.

SECTION 3 — INCOME STATEMENT DEEP DIVE (3–4 slides):
 7. Revenue quality & trajectory — 5-year revenue, organic vs. inorganic, recurring vs. transactional, currency-neutral. Teach: The four questions to ask of every revenue line.
 8. Margin architecture — Gross → Operating → Net margin trends, decomposed. Mix vs. price vs. cost effects. Teach: Margin durability frameworks.
 9. Operating leverage in action — % change in operating income vs. % change in revenue. Teach: Why operating leverage cuts both ways.
 10. Earnings quality flags — GAAP vs. non-GAAP bridge. SBC magnitude. One-time charges pattern. Common Mistake: Trusting "adjusted" EPS without auditing the bridge.

SECTION 4 — BALANCE SHEET DEEP DIVE (3 slides):
 11. Asset composition & quality — Cash, receivables, inventory, PP&E, goodwill, intangibles. DSO, DIO, DPO trends. Teach: Why working capital is a leading indicator.
 12. Liability structure & solvency — Total debt, maturities, fixed vs. floating, covenants. Net debt/EBITDA, interest coverage. Teach: Solvency vs. liquidity.
 13. Capital structure & equity — Share count history (buybacks vs. dilution), book value per share, tangible book value. Teach: SBC's true cost = dilution.

SECTION 5 — CASH FLOW DEEP DIVE (2–3 slides):
 14. Operating cash flow vs. net income — The reconciliation. Cash conversion ratio. Teach: When OCF ≠ Net Income, ask why.
 15. Free cash flow & FCF yield — FCF = OCF − CapEx (maintenance vs. growth CapEx). FCF yield vs. P/E. Teach: Owner earnings (Buffett).
 16. Capital allocation scorecard — Where the cash went over 5 years (Reinvestment / M&A / dividends / buybacks / debt paydown). Score management's discipline. Teach: Capital allocation IS the CEO's job.

SECTION 6 — RETURNS ON CAPITAL (2 slides):
 17. The DuPont decomposition — ROE = Net Margin × Asset Turnover × Leverage. Show math and trend. Teach: Why decomposed ROE tells you the kind of business.
 18. ROIC vs. WACC — Calculate ROIC. Estimate WACC. Spread = value creation or destruction. Teach: A growing company with ROIC < WACC destroys value faster than a shrinking one.

SECTION 7 — GROWTH ANALYSIS (2 slides):
 19. Historical growth quality — Revenue / EBITDA / EPS / FCF CAGR (3Y, 5Y, 10Y if available). Teach: Why FCF/share growth is the most honest of the four.
 20. Sustainability of growth — Reinvestment rate × ROIC = sustainable growth rate. Compare to actual. Teach: The two engines of long-term growth (and why one is fake).

SECTION 8 — VALUATION (3–4 slides):
 21. Multiples — what the market is paying — P/E, EV/EBITDA, EV/Sales, P/B, P/FCF. Current vs. 5-year median vs. peer median. Teach: A multiple is just a shortcut for a DCF.
 22. DCF — the model behind the multiple — Build a 3-scenario reverse DCF: what growth + margin + WACC does the current price imply? Teach: Reverse DCF.
 23. Comparables / peer table — 5–8 peers, key multiples + fundamentals (growth, margin, ROIC) side-by-side. Teach: A multiple without a fundamental match is meaningless.
 24. Margin of safety — Bull / Base / Bear price targets. Implied IRR at current price for each. Teach: Graham's margin-of-safety, applied numerically.

SECTION 9 — QUALITY & RISK SCORECARD (2 slides):
 25. Quality scorecard — Piotroski F-Score (0–9), Altman Z-Score, Beneish M-Score. Show inputs and interpretation. Teach: Three free, decades-tested screens institutional investors use.
 26. Risk register — 5–8 ranked risks (financial, operational, regulatory, competitive, governance, ESG-material). Severity × probability matrix. Teach: Risk isn't volatility — it's permanent capital impairment.

SECTION 10 — INVESTMENT THESIS SYNTHESIS (3 slides):
 27. Bull case — What has to be true. Specific, falsifiable, with target IRR.
 28. Bear case — What has to be true. Specific, falsifiable, with downside %.
 29. Base case + monitorables — Most likely path. The 3–5 metrics that, if they break, would invalidate the thesis. Teach: Pre-committing to your "I was wrong" signals.

SECTION 11 — LESSONS & SELF-ASSESSMENT (2 slides):
 30. Lessons distilled — The 5 most transferable insights from this case study about fundamental analysis as a discipline.
 31. Self-assessment quiz — 8–10 questions tied to specific slides (calculation, definition, interpretation). Provide answers in the quiz_answers field.
</deck_structure>

<slide_format>
Each slide is one object in the `slides` array of the returned JSON. Fields:

- `slide_number` (int, sequential from 1)
- `section_number` (int, 1–11)
- `section_title` (string — the section header, e.g. "Setup & Orientation")
- `title` (string — the slide headline, e.g. "Margin Architecture: Where Did the Operating Leverage Go?")
- `subtitle` (string, optional — one-line subheadline)
- `kind` (string — one of: "cover" | "exec_summary" | "how_to_read" | "content" | "peer_table" | "risk_matrix" | "quiz" | "quiz_answers")
- `body` (string, optional — 1–4 sentences of narrative analysis)
- `bullets` (array of strings, optional — 3–6 tight bullets; each bullet must be specific, not a generality)
- `formula` (object, optional — `{name, expression, inputs, result, interpretation}` for slides introducing a metric)
- `table` (object, optional — `{headers: [...], rows: [[...], ...], caption}` — use for peer tables, multi-year financials, scorecards)
- `lesson_card` (string, optional — the transferable insight; appears as a green callout)
- `common_mistake` (string, optional — appears as a red callout)
- `quick_check` (string, optional — appears as a blue callout, a question the reader can answer from the slide)
- `data_gaps` (array of strings, optional — items the user did NOT provide that would sharpen this slide)

Cover slide (slide 1) MUST use kind="cover" and include subtitle as the one-line thesis teaser.
Executive summary (slide 2) MUST use kind="exec_summary" with a `table` of key metrics.
"How to read this deck" (slide 3) MUST use kind="how_to_read" with bullets explaining the 3 pedagogical icons.
Peer comparable slide MUST use kind="peer_table" with a populated `table`.
Risk register slide MUST use kind="risk_matrix" with a `table` of risks.
Quiz slide MUST use kind="quiz" with 8–10 numbered questions in `bullets`.
Add ONE additional kind="quiz_answers" slide immediately after the quiz with the answer key in `bullets`.

Every slide must have a `title`. Every content slide should have either `body`, `bullets`, `table`, or `formula` (or a combination).

Total slide count should land between 28 and 34, matching the section anchors above (cover + exec summary + how-to-read + 2 business + 3 income + 3 balance sheet + 2–3 cash flow + 2 returns + 2 growth + 3–4 valuation + 2 quality/risk + 3 thesis + 2 lessons/quiz, plus a quiz_answers slide).
</slide_format>

<top_level_fields>
The returned JSON must also include:

- `company`: object with `name`, `ticker`, `sector`, `industry`, `as_of` (date string), `currency` (e.g. "USD")
- `thesis_tag`: one of "Bull", "Base", "Bear"
- `confidence_pct`: integer 0–100
- `one_line_thesis`: a single sentence
- `slides`: array of slide objects as defined above

Output only this JSON. Do NOT include markdown, prose, or commentary outside the JSON.
</top_level_fields>

<discipline>
- Never fabricate numbers. If a figure is not in the uploaded documents, use a "[DATA NOT PROVIDED — ...]" marker in the value and flag it in `data_gaps`.
- Numbers must be quoted with units and periods ("$48.4B revenue, FY2024" not "48").
- Every percentage must show its inputs.
- Formulas must show the actual numbers being plugged in.
- Lesson cards must be transferable principles — not restatements of what the slide already says.
- Common mistakes must name a specific cognitive or methodological error a reader is likely to make.
</discipline>
"""


# ─── JSON schema for output_config.format ────────────────────────────────────
# Pure JSON schema (no Pydantic) so we can pass directly to the API and keep
# the slide model open enough to handle optional fields gracefully.
SLIDE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "company": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "name": {"type": "string"},
                "ticker": {"type": "string"},
                "sector": {"type": "string"},
                "industry": {"type": "string"},
                "as_of": {"type": "string"},
                "currency": {"type": "string"},
            },
            "required": ["name", "ticker", "sector", "industry", "as_of", "currency"],
        },
        "thesis_tag": {"type": "string", "enum": ["Bull", "Base", "Bear"]},
        "confidence_pct": {"type": "integer"},
        "one_line_thesis": {"type": "string"},
        "slides": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "slide_number": {"type": "integer"},
                    "section_number": {"type": "integer"},
                    "section_title": {"type": "string"},
                    "title": {"type": "string"},
                    "subtitle": {"type": "string"},
                    "kind": {
                        "type": "string",
                        "enum": [
                            "cover",
                            "exec_summary",
                            "how_to_read",
                            "content",
                            "peer_table",
                            "risk_matrix",
                            "quiz",
                            "quiz_answers",
                        ],
                    },
                    "body": {"type": "string"},
                    "bullets": {"type": "array", "items": {"type": "string"}},
                    "formula": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "name": {"type": "string"},
                            "expression": {"type": "string"},
                            "inputs": {"type": "string"},
                            "result": {"type": "string"},
                            "interpretation": {"type": "string"},
                        },
                        "required": ["name", "expression"],
                    },
                    "table": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "headers": {"type": "array", "items": {"type": "string"}},
                            "rows": {
                                "type": "array",
                                "items": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                            },
                            "caption": {"type": "string"},
                        },
                        "required": ["headers", "rows"],
                    },
                    "lesson_card": {"type": "string"},
                    "common_mistake": {"type": "string"},
                    "quick_check": {"type": "string"},
                    "data_gaps": {"type": "array", "items": {"type": "string"}},
                },
                "required": [
                    "slide_number",
                    "section_number",
                    "section_title",
                    "title",
                    "kind",
                ],
            },
        },
    },
    "required": [
        "company",
        "thesis_tag",
        "confidence_pct",
        "one_line_thesis",
        "slides",
    ],
}


# ─── Job state ───────────────────────────────────────────────────────────────
@dataclass
class DeckJob:
    job_id: str
    company_name: str
    ticker: str
    status: str = "queued"  # queued | running | done | error
    message: str = ""
    output_path: str | None = None
    error: str | None = None
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None


JOBS: dict[str, DeckJob] = {}
_LOCK = threading.Lock()


def create_job(company_name: str, ticker: str) -> DeckJob:
    job = DeckJob(
        job_id=uuid.uuid4().hex,
        company_name=company_name,
        ticker=ticker,
    )
    with _LOCK:
        JOBS[job.job_id] = job
    return job


def get_job(job_id: str) -> DeckJob | None:
    with _LOCK:
        return JOBS.get(job_id)


# ─── Anthropic call ──────────────────────────────────────────────────────────
def _build_user_prompt(
    company_name: str, ticker: str, docs: list[ParsedDocument]
) -> str:
    doc_block = render_for_prompt(docs) if docs else "(no documents provided)"
    return (
        f"Build the full study-grade fundamental-analysis slide deck for:\n\n"
        f"Company: {company_name}\n"
        f"Ticker: {ticker}\n\n"
        f"Source materials follow. Triangulate every quantitative claim against them; "
        f"never invent numbers; flag missing data explicitly per the discipline rules.\n\n"
        f"{doc_block}\n\n"
        f"Now return the full deck as JSON matching the declared schema. "
        f"Output JSON only — no prose, no markdown fences."
    )


def generate_deck_spec(
    company_name: str,
    ticker: str,
    docs: list[ParsedDocument],
) -> dict[str, Any]:
    """Call Claude and return the parsed deck JSON. Streams to avoid timeouts."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set. Add it to the environment "
            "before generating decks."
        )

    client = anthropic.Anthropic(api_key=api_key)
    user_prompt = _build_user_prompt(company_name, ticker, docs)

    # Cache the long system prompt across runs — only company-specific user
    # content varies, so subsequent runs read the system prefix from cache.
    system = [
        {
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }
    ]

    with client.messages.stream(
        model=MODEL,
        max_tokens=32000,
        system=system,
        messages=[{"role": "user", "content": user_prompt}],
        output_config={
            "format": {
                "type": "json_schema",
                "schema": SLIDE_SCHEMA,
            }
        },
    ) as stream:
        final = stream.get_final_message()

    # output_config.format guarantees the first text block is valid JSON.
    text = next((b.text for b in final.content if b.type == "text"), None)
    if not text:
        raise RuntimeError("Claude returned no text content for the deck.")
    return json.loads(text)


# ─── Background worker ───────────────────────────────────────────────────────
def run_job(
    job_id: str,
    company_name: str,
    ticker: str,
    docs: list[ParsedDocument],
    output_dir: Path,
) -> None:
    """Run inside a BackgroundTasks worker. Builds the deck and writes a .pptx."""
    # Imported here to avoid a circular import: deck_builder imports nothing from
    # this module, but keeping the import local also defers python-pptx loading
    # until a job actually runs.
    from app.services.deck_builder import build_pptx

    job = get_job(job_id)
    if job is None:
        return

    try:
        job.status = "running"
        job.message = "Calling Claude to build the deck spec…"
        spec = generate_deck_spec(company_name, ticker, docs)

        job.message = "Rendering PowerPoint…"
        output_dir.mkdir(parents=True, exist_ok=True)
        safe_ticker = "".join(c for c in ticker if c.isalnum()) or "DECK"
        out_path = output_dir / f"{safe_ticker}_{job.job_id[:8]}.pptx"
        build_pptx(spec, out_path)

        job.output_path = str(out_path)
        job.status = "done"
        job.message = f"Generated {len(spec.get('slides', []))} slides."
    except Exception as e:
        job.status = "error"
        job.error = f"{type(e).__name__}: {e}"
        job.message = "Generation failed."
        # Stash trace in stderr for the operator to see; UI gets the short error.
        traceback.print_exc()
    finally:
        job.finished_at = time.time()
