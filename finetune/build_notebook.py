"""Generates router_finetune.ipynb (kept as code so the notebook is reviewable in diffs).
Run:  python finetune/build_notebook.py
"""
import json
from pathlib import Path

cells = []


def md(s):
    cells.append({"cell_type": "markdown", "metadata": {}, "source": s.strip("\n")})


def code(s):
    cells.append({"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [], "source": s.strip("\n")})


md("""
# Fine-tuning a 1.5B router model — Thistle & Pine support system

**Goal:** fine-tune **Qwen2.5-1.5B-Instruct** to classify customer messages into the 6 intents the Router uses,
then compare against (a) the same model before fine-tuning and (b) a large API model (GPT-OSS-120B on Groq).

| label | goes to |
|---|---|
| `order_tracking`, `order_action` | Orders agent |
| `product_question`, `policy_question`, `chit_chat` | Support agent (RAG) |
| `escalation` | Escalation agent |

**Data:** 1,080 train / 120 val generated from templates with noise; **120 hand-written test messages** written
separately from the templates, so the score measures generalisation, not memorisation.

**Runtime:** Colab → Runtime → Change runtime type → **T4 GPU**. Whole notebook ≈ 20–30 min.

**Optional secrets** (Colab 🔑 sidebar): `GROQ_API_KEY` (big-model comparison), `HF_TOKEN` (push model to Hugging Face).
""")

code("""
%%capture
!pip install unsloth
!pip install scikit-learn groq
""")

md("## 1. Get the data")
code("""
import os, json, pathlib
REPO_URL = ""   # e.g. "https://github.com/<you>/cadral-ops-agent.git" — leave blank to upload files instead
if REPO_URL:
    !git clone -q {REPO_URL} repo
    DATA = pathlib.Path("repo/finetune/data")
else:
    from google.colab import files
    print("Upload train.jsonl, val.jsonl and test.jsonl from finetune/data/")
    files.upload()
    DATA = pathlib.Path(".")

load = lambda n: [json.loads(l) for l in (DATA / f"{n}.jsonl").read_text().splitlines() if l.strip()]
train, val, test = load("train"), load("val"), load("test")
print(len(train), len(val), len(test))
""")

md("## 2. Prompt + evaluation helpers")
code("""
import re, time, statistics
from sklearn.metrics import accuracy_score, f1_score, classification_report, confusion_matrix

LABELS = ["order_tracking", "order_action", "product_question", "policy_question", "escalation", "chit_chat"]
SYSTEM = \"\"\"Classify the customer message for a UK homeware shop's support system into exactly one label:
order_tracking: asking where their order is or about its delivery status
order_action: wants a refund, return, cancellation, replacement or change to their own order
product_question: asks about a product (features, care, size, stock, price)
policy_question: asks about general shop policies (delivery costs, returns rules, payments, discounts, contact)
escalation: angry, threatens legal action or chargeback, reports injury or a safety hazard, or demands a human
chit_chat: greetings, thanks or off-topic
Reply with the label only.\"\"\"

def to_messages(text, label=None):
    m = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": text}]
    if label: m.append({"role": "assistant", "content": label})
    return m

def parse_label(out):
    o = out.lower().strip().replace(" ", "_").replace("-", "_")
    for l in LABELS:
        if l in o: return l
    for l in LABELS:  # loose match e.g. "order tracking" -> handled above; "escalate" -> escalation
        if l.split("_")[0][:5] in o: return l
    return "unknown"

def report(name, y_true, y_pred, latencies, cost_per_1k):
    acc = accuracy_score(y_true, y_pred); f1 = f1_score(y_true, y_pred, average="macro", labels=LABELS)
    print(f"=== {name}:  accuracy {acc:.1%}   macro-F1 {f1:.3f}   median latency {statistics.median(latencies):.0f} ms")
    print(classification_report(y_true, y_pred, labels=LABELS, zero_division=0))
    return {"model": name, "accuracy": round(acc, 4), "macro_f1": round(f1, 4),
            "median_latency_ms": round(statistics.median(latencies), 1),
            "p95_latency_ms": round(sorted(latencies)[int(len(latencies) * .95) - 1], 1),
            "cost_per_1k_msgs_usd": cost_per_1k,
            "confusion": confusion_matrix(y_true, y_pred, labels=LABELS).tolist()}
""")

md("## 3. Load the base model (4-bit) and measure it *before* fine-tuning")
code("""
import torch
from unsloth import FastLanguageModel
BASE = "unsloth/Qwen2.5-1.5B-Instruct"
model, tokenizer = FastLanguageModel.from_pretrained(BASE, max_seq_length=512, load_in_4bit=True)
FastLanguageModel.for_inference(model)

@torch.no_grad()
def classify(text):
    ids = tokenizer.apply_chat_template(to_messages(text), add_generation_prompt=True, return_tensors="pt").to("cuda")
    t0 = time.perf_counter()
    out = model.generate(input_ids=ids, max_new_tokens=8, do_sample=False, pad_token_id=tokenizer.eos_token_id)
    torch.cuda.synchronize(); ms = (time.perf_counter() - t0) * 1000
    return parse_label(tokenizer.decode(out[0, ids.shape[1]:], skip_special_tokens=True)), ms

def run_local(name):
    preds, lats = zip(*[classify(r["text"]) for r in test])
    # Self-hosted cost: T4 on-demand ≈ $0.35/hr; cost of 1k sequential messages at measured latency.
    cost = round(statistics.median(lats) / 1000 * 1000 / 3600 * 0.35, 4)
    return report(name, [r["label"] for r in test], list(preds), list(lats), cost), list(preds)

results = {}
results["base"], base_preds = run_local("Qwen2.5-1.5B base (zero-shot)")
""")

md("## 4. Fine-tune with LoRA")
code("""
from datasets import Dataset
from trl import SFTTrainer, SFTConfig
from unsloth.chat_templates import train_on_responses_only

model = FastLanguageModel.get_peft_model(
    model, r=16, lora_alpha=16, lora_dropout=0, bias="none",
    target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
    use_gradient_checkpointing="unsloth", random_state=42)

fmt = lambda rows: Dataset.from_list([{"text": tokenizer.apply_chat_template(to_messages(r["text"], r["label"]), tokenize=False)} for r in rows])
trainer = SFTTrainer(
    model=model, tokenizer=tokenizer, train_dataset=fmt(train), eval_dataset=fmt(val),
    args=SFTConfig(dataset_text_field="text", max_seq_length=512, per_device_train_batch_size=16,
                   gradient_accumulation_steps=1, num_train_epochs=2, learning_rate=2e-4, warmup_ratio=0.05,
                   lr_scheduler_type="cosine", logging_steps=10, eval_strategy="epoch", optim="adamw_8bit",
                   weight_decay=0.01, seed=42, output_dir="outputs", report_to="none"))
# Only learn to produce the label, not to repeat the prompt.
trainer = train_on_responses_only(trainer, instruction_part="<|im_start|>user\\n", response_part="<|im_start|>assistant\\n")
t0 = time.time(); stats = trainer.train(); train_minutes = (time.time() - t0) / 60
print(f"trained in {train_minutes:.1f} min")
""")

md("## 5. Measure the fine-tuned model on the same test set")
code("""
FastLanguageModel.for_inference(model)
results["finetuned"], ft_preds = run_local("Qwen2.5-1.5B fine-tuned (LoRA)")
results["finetuned"]["train_minutes_t4"] = round(train_minutes, 1)
""")

md("## 6. Large API model for comparison (GPT-OSS-120B on Groq) — optional")
code("""
from google.colab import userdata
try:
    GROQ_KEY = userdata.get("GROQ_API_KEY")
except Exception:
    GROQ_KEY = None
if GROQ_KEY:
    from groq import Groq
    g = Groq(api_key=GROQ_KEY)
    preds, lats, toks_in, toks_out = [], [], 0, 0
    for r in test:
        t0 = time.perf_counter()
        for attempt in range(5):
            try:
                resp = g.chat.completions.create(model="openai/gpt-oss-120b", messages=to_messages(r["text"]),
                                                 max_tokens=400, temperature=0, reasoning_effort="low"); break
            except Exception as e:
                time.sleep(2 * (attempt + 1))
        lats.append((time.perf_counter() - t0) * 1000)
        preds.append(parse_label(resp.choices[0].message.content))
        toks_in += resp.usage.prompt_tokens; toks_out += resp.usage.completion_tokens
        time.sleep(2.1)  # stay under the free-tier 30 requests/minute limit
    cost_1k = round((toks_in * 0.15 + toks_out * 0.60) / 1e6 / len(test) * 1000, 4)
    results["large_api"] = report("GPT-OSS-120B via Groq API", [r["label"] for r in test], preds, lats, cost_1k)
else:
    print("No GROQ_API_KEY secret — skipping large-model comparison")
""")

md("## 7. Results")
code("""
import pandas as pd
cols = ["model", "accuracy", "macro_f1", "median_latency_ms", "p95_latency_ms", "cost_per_1k_msgs_usd"]
df = pd.DataFrame([{k: v[k] for k in cols} for v in results.values()])
display(df)
# Messages the base model got wrong but the fine-tuned model fixed
fixed = [(r["text"], b, f) for r, b, f in zip(test, base_preds, ft_preds) if b != r["label"] and f == r["label"]]
still_wrong = [(r["text"], r["label"], f) for r, f in zip(test, ft_preds) if f != r["label"]]
print(f"\\nFixed by fine-tuning: {len(fixed)}   Still wrong: {len(still_wrong)}")
for t, gold, p in still_wrong: print(f"  [{gold} -> {p}] {t}")
json.dump({"results": results, "still_wrong": still_wrong}, open("finetune_results.json", "w"), indent=1)
""")

md("## 8. Save the model (GGUF for CPU serving in the backend) and download results")
code("""
model.save_pretrained("router-lora"); tokenizer.save_pretrained("router-lora")
model.save_pretrained_gguf("router-gguf", tokenizer, quantization_method="q4_k_m")
try:
    HF_TOKEN = userdata.get("HF_TOKEN")
except Exception:
    HF_TOKEN = None
if HF_TOKEN:
    HF_REPO = "your-hf-username/tp-router-qwen2.5-1.5b"   # <- change
    model.push_to_hub_gguf(HF_REPO, tokenizer, quantization_method="q4_k_m", token=HF_TOKEN)
    print("pushed to https://huggingface.co/" + HF_REPO)
from google.colab import files
files.download("finetune_results.json")
""")

nb = {"cells": cells, "metadata": {"accelerator": "GPU", "colab": {"provenance": [], "gpuType": "T4"},
      "kernelspec": {"display_name": "Python 3", "name": "python3"}, "language_info": {"name": "python"}},
      "nbformat": 4, "nbformat_minor": 0}
out = Path(__file__).parent / "router_finetune.ipynb"
out.write_text(json.dumps(nb, indent=1), encoding="utf-8")
print("wrote", out)
