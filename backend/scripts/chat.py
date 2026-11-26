"""Talk to the agent team from the terminal.

  python -m scripts.chat --as emma.clarke@example.com "the frying pan handle is loose, I want a refund"
  python -m scripts.chat                      # interactive; type 'quit' to exit
Shows the route, every tool call with the agent's reason, escalations, cost and latency.
"""
import argparse
import json

from app.agents.orchestrator import handle_turn


def show(r):
    print(f"\n[{r.route.get('agent')} via {r.route.get('source')}: {r.route.get('reason')}]")
    for st in r.steps:
        if st["type"] == "tool":
            res = json.dumps(st["result"], default=str)
            print(f"  🔧 {st['tool']}({json.dumps(st['args'])})  why: {st['why']}\n     → {res[:220]}")
        elif st["type"] == "retrieval":
            print(f"  🔎 retrieval via {st.get('backend')} top score {st.get('top_score')} idk={st.get('idk')}")
        else:
            print(f"  • {st['type']}: {st.get('why', '')}")
    for e in r.escalations:
        print(f"  🚨 escalation #{e['escalation_id']} {e['severity']}/{e['category']}: {e['summary'][:160]}")
    for a in r.actions:
        print(f"  💾 {a}")
    print(f"\nAI ({r.agent}): {r.reply}")
    for c in r.citations:
        print(f"   [{c['n']}] {c['title']} — {c['location']}")
    u = r.usage
    print(f"   ({u.get('llm_calls')} LLM calls, {u.get('prompt_tokens')}+{u.get('completion_tokens')} tokens, "
          f"${u.get('cost_usd')}, {u.get('latency_ms')} ms, status {r.status}{', ERROR ' + r.error if r.error else ''})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("message", nargs="?")
    ap.add_argument("--as", dest="email", help="signed-in customer email")
    ap.add_argument("--conv", help="continue an existing conversation id")
    a = ap.parse_args()
    conv = a.conv
    if a.message:
        r = handle_turn(a.message, conversation_id=conv, customer_email=a.email)
        show(r)
        print(f"\nconversation: {r.conversation_id}")
        return
    while (msg := input("\nYou: ").strip()) not in ("quit", "exit"):
        r = handle_turn(msg, conversation_id=conv, customer_email=a.email)
        conv = r.conversation_id
        show(r)


if __name__ == "__main__":
    main()
