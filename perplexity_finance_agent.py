"""
Perplexity Finance Agent
========================
A financial analysis agent powered by Claude (claude-opus-4-6) that sources
ALL real-time data exclusively from the Perplexity Finance API.

Usage:
    python perplexity_finance_agent.py                      # interactive REPL
    python perplexity_finance_agent.py "AAPL stock price"   # single query

Environment variables required:
    ANTHROPIC_API_KEY   - Your Anthropic API key
    PERPLEXITY_API_KEY  - Your Perplexity API key
"""

import os
import sys
import json
import httpx
import anthropic

# ---------------------------------------------------------------------------
# Perplexity Finance integration
# ---------------------------------------------------------------------------

PERPLEXITY_API_URL = "https://api.perplexity.ai/chat/completions"
PERPLEXITY_MODEL = "sonar-pro"  # sonar-pro for richer, cited financial data

# Finance-authoritative domains only
FINANCE_DOMAINS = [
    "finance.yahoo.com",
    "bloomberg.com",
    "reuters.com",
    "marketwatch.com",
    "wsj.com",
    "cnbc.com",
    "seekingalpha.com",
    "investing.com",
    "finviz.com",
    "macrotrends.net",
]


def query_perplexity_finance(query: str) -> str:
    """
    Send a financial query to Perplexity Finance and return the answer
    with inline citations.

    Returns a plain-text string containing the answer and source URLs.
    """
    api_key = os.environ.get("PERPLEXITY_API_KEY", "").strip()
    if not api_key:
        return (
            "ERROR: PERPLEXITY_API_KEY environment variable is not set. "
            "Please export your Perplexity API key and retry."
        )

    payload = {
        "model": PERPLEXITY_MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a real-time financial data assistant. "
                    "Provide precise, factual financial data: stock prices, "
                    "market caps, P/E ratios, EPS, revenue, earnings, analyst "
                    "ratings, price targets, and macroeconomic indicators. "
                    "Always include the exact figures and the date/time they were "
                    "retrieved. Be concise and structured."
                ),
            },
            {"role": "user", "content": query},
        ],
        "search_domain_filter": FINANCE_DOMAINS,
        "return_citations": True,
        "search_recency_filter": "day",  # freshest data available
        "temperature": 0.1,  # low temperature for factual accuracy
    }

    try:
        with httpx.Client(timeout=45.0) as client:
            response = client.post(
                PERPLEXITY_API_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        return f"ERROR: Perplexity API returned {exc.response.status_code}: {exc.response.text}"
    except httpx.RequestError as exc:
        return f"ERROR: Network error contacting Perplexity API: {exc}"

    data = response.json()
    content: str = data["choices"][0]["message"]["content"]
    citations: list[str] = data.get("citations", [])

    if citations:
        sources = "\n".join(f"  [{i+1}] {url}" for i, url in enumerate(citations[:8]))
        content = f"{content}\n\nSources:\n{sources}"

    return content


# ---------------------------------------------------------------------------
# Tool definitions for Claude
# ---------------------------------------------------------------------------

TOOLS: list[dict] = [
    {
        "name": "query_perplexity_finance",
        "description": (
            "Fetch real-time financial data exclusively from Perplexity Finance. "
            "Use this for ANY financial question: current stock prices, market cap, "
            "P/E ratio, EPS, revenue, earnings reports, analyst ratings, price "
            "targets, options flow, sector performance, macroeconomic data, "
            "cryptocurrency prices, and breaking market news. "
            "This is the ONLY permitted data source — always call this tool "
            "instead of relying on training knowledge for any financial figures."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "A precise financial question or data request. "
                        "Examples: 'Current stock price and 52-week range for NVDA', "
                        "'Apple Q1 2025 earnings: EPS and revenue vs estimates', "
                        "'S&P 500 current level and year-to-date performance', "
                        "'Tesla analyst consensus price target and ratings breakdown'"
                    ),
                }
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    }
]

SYSTEM_PROMPT = """\
You are a professional financial analysis agent. Your knowledge cutoff means your \
training data is stale — therefore you MUST call query_perplexity_finance for \
every piece of financial data you reference. Never quote prices, ratios, earnings, \
or any market figures from memory.

Workflow:
1. Understand the user's financial question.
2. Decompose it into focused sub-queries if needed.
3. Call query_perplexity_finance one or more times to gather real-time data.
4. Synthesize a clear, well-structured response with the retrieved figures.
5. Cite the sources returned by Perplexity inline where relevant.

Response style:
- Lead with the key numbers the user asked for.
- Use tables or bullet lists for multi-metric comparisons.
- Flag any data caveats (e.g., delayed quotes, estimated figures).
- Keep responses concise but complete.\
"""


# ---------------------------------------------------------------------------
# Agent loop
# ---------------------------------------------------------------------------

def run_agent(user_query: str, verbose: bool = False) -> str:
    """
    Run the Perplexity Finance agent for a single user query.
    Returns the final text response.
    """
    client = anthropic.Anthropic()

    messages: list[dict] = [{"role": "user", "content": user_query}]

    while True:
        with client.messages.stream(
            model="claude-opus-4-6",
            max_tokens=4096,
            thinking={"type": "adaptive"},
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        ) as stream:
            response = stream.get_final_message()

        if verbose:
            print(f"[debug] stop_reason={response.stop_reason}", file=sys.stderr)

        # Claude finished — extract and return the text
        if response.stop_reason == "end_turn":
            for block in response.content:
                if block.type == "text":
                    return block.text
            return ""

        # Claude wants to call a tool
        if response.stop_reason == "tool_use":
            # Append Claude's response (including tool_use blocks)
            messages.append({"role": "assistant", "content": response.content})

            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue

                if verbose:
                    print(
                        f"[debug] tool_call: {block.name}({json.dumps(block.input)})",
                        file=sys.stderr,
                    )

                if block.name == "query_perplexity_finance":
                    result = query_perplexity_finance(block.input["query"])
                else:
                    result = f"ERROR: Unknown tool '{block.name}'"

                if verbose:
                    preview = result[:200].replace("\n", " ")
                    print(f"[debug] tool_result preview: {preview}…", file=sys.stderr)

                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    }
                )

            messages.append({"role": "user", "content": tool_results})
            continue

        # Unexpected stop reason
        return f"Agent stopped unexpectedly (stop_reason={response.stop_reason})."


# ---------------------------------------------------------------------------
# Interactive REPL
# ---------------------------------------------------------------------------

def repl() -> None:
    print("Perplexity Finance Agent  (type 'exit' or Ctrl-C to quit)\n")
    while True:
        try:
            query = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye.")
            break

        if not query:
            continue
        if query.lower() in {"exit", "quit", "q"}:
            print("Goodbye.")
            break

        print()
        answer = run_agent(query, verbose="--verbose" in sys.argv)
        print(f"Agent: {answer}\n")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]

    if args:
        # Single query from CLI: python perplexity_finance_agent.py "AAPL price"
        user_input = " ".join(args)
        result = run_agent(user_input, verbose="--verbose" in sys.argv)
        print(result)
    else:
        repl()
