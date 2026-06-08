# scripts/test_faq.py
"""
Exercise the local RAG pipeline without making a phone call.

Run from project root:
  python scripts/test_faq.py build
  python scripts/test_faq.py ask "How much is a consultation?"
  python scripts/test_faq.py chat
"""

import argparse
import asyncio
import sys
import time
from pathlib import Path

# Ensure we can import from the project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.faq import build_index, search_faq

async def _cmd_build():
    print("Building LanceDB vector index from clinic_faq.md...")
    build_index()
    print("Done!")

async def _cmd_ask(query: str):
    print(f"\nQuestion: {query}")
    print("-" * 50)
    
    start_time = time.perf_counter()
    result = await search_faq(query)
    end_time = time.perf_counter()
    
    print(f"Result:\n{result}\n")
    print(f"[Search completed in {end_time - start_time:.3f} seconds]")

async def _cmd_chat():
    print("FAQ Interactive Search (Type 'exit' to quit)")
    print("-" * 50)
    
    while True:
        try:
            query = input("\nAsk a question: ")
            if query.lower() in ("exit", "quit"):
                break
            if not query.strip():
                continue

            start_time = time.perf_counter()
            result = await search_faq(query)
            elapsed = time.perf_counter() - start_time

            print(f"\nAria's Knowledge Context:\n{result}")
            print(f"\n[Latency: {elapsed:.3f}s]")
            
        except KeyboardInterrupt:
            break
        except EOFError:
            break

async def main():
    parser = argparse.ArgumentParser(description="Test the local RAG pipeline.")
    sub = parser.add_subparsers(dest="action", required=True)

    sub.add_parser("build", help="Rebuild the LanceDB index")
    
    ask = sub.add_parser("ask", help="Ask a single question")
    ask.add_argument("query", type=str, help="The question to ask")
    
    sub.add_parser("chat", help="Start an interactive FAQ testing loop")

    args = parser.parse_args()

    if args.action == "build":
        await _cmd_build()
    elif args.action == "ask":
        await _cmd_ask(args.query)
    elif args.action == "chat":
        await _cmd_chat()

if __name__ == "__main__":
    asyncio.run(main())