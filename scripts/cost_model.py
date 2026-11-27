"""Unit-economics calculator for the OmniCare + mem-guard + NanoMemory stack.

Prints a markdown table you can paste into a pricing review. Every number is an explicit
assumption in the CLI, so a pricing decision can be re-derived instead of remembered.

    python scripts/cost_model.py --users 10000 --price-per-user 3.0
"""

from __future__ import annotations

import argparse
import json


def compute(args: argparse.Namespace) -> dict:
    messages = args.messages_per_user
    llm_input_tokens = messages * args.input_tokens
    llm_output_tokens = messages * args.output_tokens
    embedding_tokens = args.writes_per_user * args.embed_tokens_per_write + (
        messages * args.query_embed_tokens
    )
    storage_gb = args.users * args.memories_per_user * args.bytes_per_memory / 1e9

    llm_input_cost = llm_input_tokens / 1_000_000 * args.input_price
    llm_output_cost = llm_output_tokens / 1_000_000 * args.output_price
    embedding_cost = embedding_tokens / 1_000_000 * args.embedding_price
    storage_cost = storage_gb * args.storage_price_gb_month
    variable = llm_input_cost + llm_output_cost + embedding_cost + storage_cost
    fixed = args.fixed_infra
    total = variable + fixed
    revenue = args.users * args.price_per_user
    return {
        "messages": messages,
        "llm_input_tokens": llm_input_tokens,
        "llm_output_tokens": llm_output_tokens,
        "embedding_tokens": embedding_tokens,
        "llm_input_cost": llm_input_cost,
        "llm_output_cost": llm_output_cost,
        "embedding_cost": embedding_cost,
        "storage_gb": storage_gb,
        "storage_cost": storage_cost,
        "variable": variable,
        "fixed": fixed,
        "total": total,
        "per_user": total / args.users if args.users else 0.0,
        "revenue": revenue,
        "margin": (revenue - total) / revenue if revenue else 0.0,
    }


def render(args: argparse.Namespace, result: dict) -> str:
    def money(value: float) -> str:
        return f"${value:,.4f}" if value < 1 else f"${value:,.2f}"

    lines = [
        f"# Unit economics -- {args.users:,} users",
        "",
        "| Line | Monthly volume | Unit price | Cost |",
        "| --- | --- | --- | --- |",
        f"| LLM input | {result['llm_input_tokens']:,} tokens | ${args.input_price}/1M | {money(result['llm_input_cost'])} |",
        f"| LLM output | {result['llm_output_tokens']:,} tokens | ${args.output_price}/1M | {money(result['llm_output_cost'])} |",
        f"| Embeddings | {result['embedding_tokens']:,} tokens | ${args.embedding_price}/1M | {money(result['embedding_cost'])} |",
        f"| Vector + trace storage | {result['storage_gb']:.3f} GB | ${args.storage_price_gb_month}/GB-month | {money(result['storage_cost'])} |",
        f"| **Variable total** | | | **{money(result['variable'])}** |",
        f"| Fixed infrastructure | | | {money(result['fixed'])} |",
        f"| **Total** | | | **{money(result['total'])}** |",
        "",
        f"- cost per user: **{money(result['per_user'])} / month**",
        f"- revenue at ${args.price_per_user}/user: {money(result['revenue'])}",
        f"- gross margin: **{result['margin']:.1%}**",
        "",
        "## Assumptions",
        "",
        f"- {result['messages']} chat messages per user per month",
        f"- {args.input_tokens} input tokens and {args.output_tokens} output tokens per message",
        f"- {args.writes_per_user} memory writes/user/month at {args.embed_tokens_per_write} embedding tokens each",
        f"- {args.query_embed_tokens} embedding tokens per query",
        f"- {args.memories_per_user} memories/user at {args.bytes_per_memory} bytes",
        f"- fixed infrastructure {money(args.fixed_infra)}/month (API, gateway, database, monitoring)",
        "",
        "Prices are inputs, not vendor quotes: pass your contract rates with --input-price etc.",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--users", type=int, default=1000)
    parser.add_argument("--messages-per-user", type=int, default=120)
    parser.add_argument("--input-tokens", type=int, default=900)
    parser.add_argument("--output-tokens", type=int, default=260)
    parser.add_argument("--writes-per-user", type=int, default=20)
    parser.add_argument("--embed-tokens-per-write", type=int, default=180)
    parser.add_argument("--query-embed-tokens", type=int, default=40)
    parser.add_argument("--memories-per-user", type=int, default=200)
    parser.add_argument("--bytes-per-memory", type=int, default=2048)
    parser.add_argument("--input-price", type=float, default=0.14)
    parser.add_argument("--output-price", type=float, default=0.28)
    parser.add_argument("--embedding-price", type=float, default=0.02)
    parser.add_argument("--storage-price-gb-month", type=float, default=0.10)
    parser.add_argument("--fixed-infra", type=float, default=120.0)
    parser.add_argument("--price-per-user", type=float, default=3.0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = compute(args)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(render(args, result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
