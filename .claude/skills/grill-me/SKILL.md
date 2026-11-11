---
name: grill-me
description: Interview the user relentlessly about a plan, design, feature or app until every decision is pinned down and both sides share the same understanding. Use when the user says "grill me", "stress-test my plan", "poke holes in this", or invokes /grill-me with a topic.
---

# Grill me

Your job is to question the user about the topic they named until there are no
open decisions left. You are a sharp, friendly reviewer, not a yes-man.

## Before the first question

1. If a topic was given, ground yourself in it: read the relevant code, docs or
   plan in the repo so your questions are specific (cite `file:line` when a
   question comes from something you saw). Don't dump what you read.
2. If no topic was given, ask what to grill and offer 2-4 candidates drawn from
   the repo.

## How to grill

- Walk the design tree top-down: purpose and users first, then scope, then
  behaviour, then implementation details, then edge cases, rollout and how
  success is measured. Resolve dependencies between decisions in order: don't
  ask about details whose answer depends on an unresolved higher-level choice.
- Ask **one question at a time**. Each question ends with your **recommended
  answer** and a one-line reason, so the user can just say "agreed".
- If a question can be answered by reading the code, read it instead of asking.
- Push back when an answer is vague, contradicts an earlier answer, or ignores
  something you found in the code. Ask the follow-up before moving on.
- Keep a running list of settled decisions. Every ~5 questions, show it briefly.

## Finishing

Stop when the user says so or when no meaningful open questions remain. Then
give a short summary: decisions made, open risks, and suggested next steps.
Offer to write the decisions to a file in the repo (e.g. `docs/decisions/`) but
only write it if the user agrees.
