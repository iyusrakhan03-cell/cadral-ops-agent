"""Build the intent-classification dataset used to fine-tune the small router model.

Labels (what the Router needs to decide):
  order_tracking   - where is my specific order / delivery status        -> Orders agent
  order_action     - refund / return / cancel / change address on MY order -> Orders agent
  product_question - about a product: features, care, stock, price         -> Support agent (RAG)
  policy_question  - general shop policy: delivery costs, returns rules...  -> Support agent (RAG)
  escalation       - angry, legal/chargeback threat, safety/injury, demands a human -> Escalation agent
  chit_chat        - greetings, thanks, off-topic                           -> Support agent

TRAIN/VAL come from templates + slot filling + realistic noise (typos, lowercase, no punctuation).
TEST is the separate hand-written file data/test.jsonl — written independently of the templates,
so the reported accuracy measures generalisation, not template memorisation.

Run:  python finetune/make_dataset.py
"""
import json
import random
from pathlib import Path

random.seed(7)
OUT = Path(__file__).parent / "data"

ORDER_IDS = [f"TP-{n}" for n in range(100100, 100600, 7)]
PRODUCTS = ["cast iron casserole", "Pine Pro frying pan", "linen duvet cover", "stoneware dinner set", "oak chopping board",
            "soy candle", "bath towel set", "knife block", "kettle", "toaster", "storage ottoman", "bedside table",
            "wine glasses", "non-stick saucepan set", "king size fitted sheet", "reed diffuser", "table lamp",
            "bamboo bath mat", "pasta bowls", "coffee mugs", "sideboard", "Pine Pro wok", "pillow", "throw blanket"]
COUNTRIES = ["Ireland", "France", "Germany", "Spain", "the US", "Belgium", "Australia", "the Netherlands"]
PAYMENTS = ["Klarna", "PayPal", "Apple Pay", "Amex", "a gift card", "Google Pay"]
DAYS = ["3", "5", "a week", "10 days", "two weeks", "4 days"]

T = {
"order_tracking": [
    "where is my order {oid}", "hi, can you tell me where order {oid} is?", "my {product} hasnt arrived yet, order {oid}",
    "has my order shipped? its {oid}", "tracking for {oid} hasn't updated in {days}", "when will my order arrive",
    "i ordered a {product} {days} ago and nothing yet", "is order {oid} out for delivery today",
    "can i get the tracking number for my order", "my parcel says delivered but i havent got it",
    "order {oid} - what's the status please", "still waiting on my {product}, any update?",
    "did you send my order yet? number {oid}", "which courier is delivering {oid}", "estimated delivery date for {oid}?",
    "it's been {days} since I ordered, where's my stuff", "has {oid} been dispatched", "my delivery is late, order {oid}",
    "is my {product} on its way", "the tracking link isnt working for {oid}", "can you check on my order please",
    "my order {oid} seems stuck at the depot", "when is the {product} I ordered going to arrive?",
],
"order_action": [
    "i want a refund for order {oid}", "please cancel my order {oid}", "can i return the {product} from {oid}",
    "i need to change the delivery address on {oid}", "the {product} arrived broken, I'd like a refund",
    "how do i send back my {product}? order {oid}", "refund my {product} please, it's not what i expected",
    "cancel {oid} i ordered by mistake", "my {product} is faulty, can you replace it? {oid}",
    "can you update my address for order {oid} to 14 Mill Road, York", "i'd like to return part of order {oid}",
    "the {product} came damaged in the box, order {oid}", "please refund the delivery charge on {oid}",
    "i was charged twice for {oid}, can you refund one", "i returned my {product} {days} ago, where's my refund?",
    "wrong item sent, i ordered a {product}. order {oid}", "can i swap the {product} for a different colour",
    "stop my order {oid} before it ships", "my {product} has a crack in it, want my money back",
    "start a return for {oid} please", "the {product} is missing from my parcel {oid}",
    "i want to exchange my {product}", "can you resend order {oid}, it never came and i want a replacement",
],
"product_question": [
    "is the {product} dishwasher safe?", "what size is the {product}", "do you have the {product} in stock?",
    "is the {product} induction compatible", "how much is the {product}", "what is the {product} made of",
    "can the {product} go in the oven", "when will the {product} be back in stock", "what colours does the {product} come in",
    "how do i clean my {product}", "how long does the {product} candle burn for", "does the {product} come with a warranty",
    "whats the difference between your pine pro pans and the normal ones", "is the {product} suitable for a king size bed",
    "how heavy is the {product}", "do you sell {product}s", "is the {product} microwave safe",
    "how should i season a cast iron pan", "what wattage is the {product}", "can i get the {product} engraved",
    "how many does the {product} set include", "what are the dimensions of the {product}",
],
"policy_question": [
    "how much is delivery", "do you deliver to {country}?", "what is your returns policy", "how long do refunds take",
    "do you accept {payment}", "how long do I have to return something", "is there free delivery",
    "what are your opening hours", "do you have a shop i can visit", "how do pine points work",
    "can I use two discount codes", "do you offer student discount", "how much is express delivery",
    "what's the last order date for christmas", "do you price match", "do gift cards expire",
    "how do i contact customer service", "do you do gift wrapping", "is delivery carbon neutral",
    "can i pay in instalments", "what's your warranty on furniture", "do you deliver on saturdays",
    "how do I get the newsletter discount", "do you do discounts for bulk orders", "is there an NHS discount",
    "do I pay customs if I order to {country}", "how long does standard delivery take", "what does final sale mean",
],
"escalation": [
    "this is ridiculous, third time I've contacted you about {oid}. I want a manager NOW",
    "if i dont get my money back today i'm going to trading standards",
    "i'm doing a chargeback with my bank, you're useless", "the {product} caught fire in my kitchen",
    "your {product} burned my hand, this is dangerous", "i want to speak to a real person not a bot",
    "absolutely disgusting service. never shopping here again. sort {oid} out or i'll post everywhere",
    "I'm contacting my solicitor about this", "the kettle sparked and tripped my electrics, could have killed someone",
    "WHERE IS MY ORDER?? {days} and NOTHING. worst company ever",
    "you have stolen my money, i want a full refund immediately or i'm reporting you",
    "the {product} glass shattered and cut my daughter", "let me talk to a human please, this bot isnt helping",
    "i'm furious. you charged me {days} ago and ignored 4 emails", "this is a scam, i'm reporting you to action fraud",
    "my child swallowed a piece that broke off the {product}", "i'll be leaving a 1 star review everywhere unless you fix this",
    "put me through to your complaints department", "the {product} smelled of burning plastic and smoked",
    "you lot are a joke. I want compensation for my wasted time",
],
"chit_chat": [
    "hi", "hello there", "thanks so much!", "thank you, that's all", "good morning", "are you a robot?",
    "what's the weather like in leeds", "cheers", "ok great", "bye", "can you tell me a joke", "who made you",
    "hey how are you", "thanks for your help", "lovely, appreciate it", "what's your name",
    "can you help me with my maths homework", "who won the football last night", "ok", "perfect thanks",
    "hiya", "test", "brilliant, have a good day",
],
}

TYPOS = {"order": ["oder", "ordr"], "refund": ["refnd", "refund"], "delivery": ["delivary", "dilivery"],
         "please": ["pls", "plz"], "received": ["recieved"], "arrived": ["arived"], "cancel": ["cancell"],
         "because": ["cos", "bc"], "you": ["u"], "your": ["ur"]}


def noisy(text: str) -> str:
    r = random.random()
    if r < 0.35:
        text = text.lower()
    elif r < 0.45:
        text = text[0].upper() + text[1:]
    if random.random() < 0.3:
        words = text.split()
        text = " ".join(random.choice(TYPOS[w]) if w in TYPOS and random.random() < 0.6 else w for w in words)
    if random.random() < 0.25:
        text = text.rstrip("?.!")
    if random.random() < 0.1:
        text = random.choice(["Hi, ", "Hello, ", "hey ", "Hi there - "]) + text
    if random.random() < 0.05:
        text += random.choice([" thanks", " cheers", " x", " asap"])
    return text


def fill(tpl: str) -> str:
    return tpl.format(oid=random.choice(ORDER_IDS), product=random.choice(PRODUCTS), country=random.choice(COUNTRIES),
                      payment=random.choice(PAYMENTS), days=random.choice(DAYS))


def main():
    OUT.mkdir(exist_ok=True)
    per_label = 200
    rows = []
    for label, templates in T.items():
        seen = set()
        tries = 0
        while len([r for r in rows if r["label"] == label]) < per_label and tries < 5000:
            tries += 1
            text = noisy(fill(random.choice(templates)))
            if text in seen:
                continue
            seen.add(text)
            rows.append({"text": text, "label": label})
    random.shuffle(rows)
    n_val = len(rows) // 10
    for name, part in (("val", rows[:n_val]), ("train", rows[n_val:])):
        with (OUT / f"{name}.jsonl").open("w", encoding="utf-8") as f:
            for r in part:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"{name}: {len(part)} rows")
    test = [json.loads(l) for l in (OUT / "test.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    from collections import Counter
    print(f"test (hand-written): {len(test)} rows {dict(Counter(r['label'] for r in test))}")


if __name__ == "__main__":
    main()
