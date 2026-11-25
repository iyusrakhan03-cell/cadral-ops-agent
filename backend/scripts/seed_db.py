"""Create the shop database and fill it with realistic, deterministic demo data.

- 60 products from data/docs/products.csv
- 48 customers, ~170 orders spread over the last 120 days (dates are relative to *now*,
  so the demo always looks fresh)
- 8 hand-built "story" customers, each set up to exercise one rule of the refund / order policy.

Run:  python -m scripts.seed_db         (drops and recreates all shop tables)
"""
import csv
import random
from datetime import datetime, timedelta, timezone

from app.config import get_settings
from app.db import Base, engine, session_scope
from app.models import Customer, Order, OrderEvent, OrderItem, Product, Refund

random.seed(42)
NOW = datetime.now(timezone.utc).replace(microsecond=0)

FIRST = ["Amelia", "Oliver", "Isla", "George", "Ava", "Noah", "Freya", "Arthur", "Lily", "Leo", "Poppy", "Harry",
         "Ella", "Jack", "Ivy", "Charlie", "Rosie", "Theo", "Evie", "Alfie", "Florence", "Finley", "Maya", "Archie",
         "Hannah", "Reuben", "Zara", "Callum", "Niamh", "Rhys", "Priya", "Kwame", "Chloe", "Ethan", "Aisha", "Ben",
         "Lucy", "Tom", "Megan", "Sam"]
LAST = ["Smith", "Jones", "Taylor", "Brown", "Williams", "Wilson", "Johnson", "Davies", "Robinson", "Wright",
        "Evans", "Walker", "Roberts", "Green", "Hall", "Wood", "Jackson", "Clarke", "Khan", "Turner", "Hill",
        "Moore", "Cooper", "Ward", "Morris", "King", "Watson", "Harris", "Shah", "Baker", "Kaur", "Owusu"]
CITIES = [("Leeds", "LS6"), ("Manchester", "M14"), ("Bristol", "BS7"), ("York", "YO10"), ("Sheffield", "S11"),
          ("Nottingham", "NG7"), ("Brighton", "BN2"), ("Cardiff", "CF24"), ("Edinburgh", "EH9"), ("London", "SE15"),
          ("London", "N16"), ("Birmingham", "B13"), ("Newcastle", "NE6"), ("Norwich", "NR2"), ("Bath", "BA2")]
STREETS = ["Elm Grove", "Station Road", "Victoria Street", "Church Lane", "Park Avenue", "Mill Road", "Albert Terrace",
           "Queens Road", "The Crescent", "Highfield Road", "Westbourne Gardens", "Orchard Close"]
PAYMENTS = ["Visa", "Visa", "Mastercard", "Mastercard", "PayPal", "Apple Pay", "Google Pay", "Klarna", "American Express"]


def address(city_pc=None):
    city, pc = city_pc or random.choice(CITIES)
    return f"{random.randint(1, 140)} {random.choice(STREETS)}, {city}, {pc} {random.randint(1, 9)}{random.choice('ABDEFGHJLNPQRSTUWXY')}{random.choice('ABDEFGHJLNPQRSTUWXY')}"


def load_products(s) -> list[Product]:
    products = []
    with (get_settings().docs_dir / "products.csv").open(encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            p = Product(sku=r["sku"], name=r["name"], category=r["category"], price=float(r["price_gbp"]),
                        stock_qty=int(r["stock_qty"] or 0), delivery_type=r["delivery_type"] or "standard",
                        final_sale=(r["final_sale"] or "").lower() == "yes")
            s.add(p)
            products.append(p)
    return products


def shipping_for(method: str, subtotal: float) -> float:
    return {"standard": 0.0 if subtotal >= 50 else 3.95, "express": 6.95, "2-person": 29.0,
            "highlands_islands": 8.95, "international": 0.0 if subtotal >= 150 else 14.95}[method]


def make_order(s, oid: str, cust: Customer, items: list[tuple[Product, int]], created: datetime, *,
               status: str | None = None, method: str | None = None, tracking_stuck: bool = False,
               intl_address: str | None = None) -> Order:
    if method is None:
        method = "2-person" if any(p.delivery_type == "2-person" for p, _ in items) else \
            random.choices(["standard", "express"], [0.8, 0.2])[0]
    subtotal = round(sum(p.price * q for p, q in items), 2)
    ship = shipping_for(method, subtotal)
    transit = {"standard": 4, "express": 1, "2-person": 10, "highlands_islands": 6, "international": 8}[method]
    age_days = (NOW - created).total_seconds() / 86400

    if status is None:
        if age_days < 0.5:
            status = "processing"
        elif age_days < transit:
            status = "dispatched"
        else:
            status = random.choices(["delivered", "cancelled", "returned"], [0.9, 0.04, 0.06])[0]

    courier = "DPD" if method in ("express", "2-person") else ("Royal Mail" if method != "international" else "DPD International")
    o = Order(id=oid, customer=cust, status=status, shipping_method=method, shipping_cost=ship, subtotal=subtotal,
              total=round(subtotal + ship, 2), payment_method=random.choice(PAYMENTS),
              shipping_address=intl_address or address(),
              created_at=created)
    o.items = [OrderItem(sku=p.sku, name=p.name, qty=q, unit_price=p.price) for p, q in items]
    o.events.append(OrderEvent(kind="created", detail=f"Order placed, £{o.total:.2f} via {o.payment_method}",
                               actor="customer", created_at=created))

    if status in ("dispatched", "delivered", "returned"):
        o.dispatched_at = created + timedelta(hours=random.randint(2, 20))
        o.courier = courier
        o.tracking_number = (f"TT{random.randint(100000000, 999999999)}GB" if courier == "Royal Mail"
                             else f"15{random.randint(100000000000, 999999999999)}")
        o.events.append(OrderEvent(kind="dispatched", detail=f"Dispatched with {courier}, tracking {o.tracking_number}",
                                   actor="warehouse", created_at=o.dispatched_at))
        o.tracking_status = "In transit — with courier"
    if status == "dispatched" and tracking_stuck:
        o.tracking_status = "Item received at Royal Mail Leeds Mail Centre (no update since)"
    if status in ("delivered", "returned"):
        o.delivered_at = min(o.dispatched_at + timedelta(days=transit, hours=random.randint(0, 30)), NOW - timedelta(hours=1))
        o.tracking_status = "Delivered — left in safe place" if method == "standard" else "Delivered — signed for"
        o.events.append(OrderEvent(kind="delivered", detail=o.tracking_status, actor="courier", created_at=o.delivered_at))
    if status == "returned":
        when = o.delivered_at + timedelta(days=random.randint(3, 12))
        if when < NOW:
            o.refunds.append(Refund(amount=subtotal, reason="Returned — changed mind", status="issued",
                                    created_by="human:ops", decided_by="human:ops", created_at=when))
            o.events.append(OrderEvent(kind="refund_issued", detail=f"Refund £{subtotal:.2f} after return",
                                       actor="human:ops", created_at=when))
            for it in o.items:
                it.refunded_qty = it.qty
    if status == "cancelled":
        o.events.append(OrderEvent(kind="cancelled", detail="Cancelled by customer before dispatch",
                                   actor="customer", created_at=created + timedelta(minutes=40)))
    s.add(o)
    return o


def main():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    with session_scope() as s:
        products = load_products(s)
        sellable = [p for p in products if p.category != "Gift Cards" and p.stock_qty > 0]
        small = [p for p in sellable if p.delivery_type == "standard" and not p.final_sale]
        pine_pro = [p for p in small if p.name.startswith("Pine Pro") and p.price <= 150] or small[:3]
        big = [p for p in sellable if p.delivery_type == "2-person" and p.price > 150] or \
              sorted(sellable, key=lambda p: -p.price)[:2]
        final = [p for p in sellable if p.final_sale] or small[-2:]

        # ---------------------------------------------------------- story customers (demo scenarios)
        def cust(name, email, city, phone, notes=None):
            c = Customer(name=name, email=email, city=city, phone=phone, notes=notes,
                         created_at=NOW - timedelta(days=random.randint(200, 700)))
            s.add(c)
            return c

        emma = cust("Emma Clarke", "emma.clarke@example.com", "Leeds", "07700 900101")
        make_order(s, "TP-100482", emma, [(pine_pro[0], 1)], NOW - timedelta(days=7), status="delivered", method="standard")
        # → eligible for automatic refund (delivered, within 30 days, ≤ £150, no recent refunds)

        james = cust("James Patel", "james.patel@example.com", "Manchester", "07700 900102")
        make_order(s, "TP-100517", james, [(random.choice(small), 2)], NOW - timedelta(days=11), status="dispatched",
                   method="standard", tracking_stuck=True)
        # → lost-parcel case: tracking not updated for > 5 working days

        sophie = cust("Sophie Walker", "sophie.walker@example.com", "Bristol", "07700 900103")
        make_order(s, "TP-100533", sophie, [(random.choice(small), 1), (random.choice(small), 1)],
                   NOW - timedelta(hours=2), status="processing")
        # → can still cancel or change address

        daniel = cust("Daniel Hughes", "daniel.hughes@example.com", "York", "07700 900104")
        make_order(s, "TP-100391", daniel, [(big[0], 1)], NOW - timedelta(days=17), status="delivered", method="2-person")
        # → refund > £150 needs human approval

        olivia = cust("Olivia Bennett", "olivia.bennett@example.com", "London", "07700 900105",
                      notes="Frequent returns — 3 refunds in the last 90 days.")
        for i, d in enumerate((80, 55, 30)):
            o = make_order(s, f"TP-10030{i}", olivia, [(random.choice(small), 1)], NOW - timedelta(days=d),
                           status="delivered", method="standard")
            amt = float(o.subtotal)
            o.refunds.append(Refund(amount=amt, reason="Item not as expected", status="issued", created_by="human:ops",
                                    decided_by="human:ops", created_at=o.delivered_at + timedelta(days=2)))
            o.items[0].refunded_qty = 1
        make_order(s, "TP-100498", olivia, [(random.choice(small), 1)], NOW - timedelta(days=6), status="delivered",
                   method="standard")
        # → 3 refunds in 90 days: AI must not auto-refund

        mo = cust("Mohammed Ali", "mohammed.ali@example.com", "Birmingham", "07700 900106")
        make_order(s, "TP-100215", mo, [(random.choice(small), 1)], NOW - timedelta(days=52), status="delivered",
                   method="standard")
        # → delivered > 30 days ago: outside returns window

        grace = cust("Grace Thompson", "grace.thompson@example.com", "Sheffield", "07700 900107")
        make_order(s, "TP-100505", grace, [(final[0], 1)], NOW - timedelta(days=8), status="delivered", method="standard")
        # → Final Sale item: not returnable

        liam = cust("Liam O'Connor", "liam.oconnor@example.com", "Dublin", "+353 85 555 0108")
        make_order(s, "TP-100526", liam, [(random.choice(small), 1)], NOW - timedelta(days=3), status="dispatched",
                   method="international", intl_address="22 Rathmines Road Lower, Dublin 6, D06 X2P4, Ireland")
        # → international order in transit

        # ---------------------------------------------------------- background customers & orders
        used = {f"{c.name}" for c in (emma, james, sophie, daniel, olivia, mo, grace, liam)}
        customers = []
        while len(customers) < 40:
            name = f"{random.choice(FIRST)} {random.choice(LAST)}"
            if name in used:
                continue
            used.add(name)
            city, _ = random.choice(CITIES)
            customers.append(cust(name, f"{name.lower().replace(' ', '.')}@example.com", city,
                                  f"07700 9{random.randint(10000, 99999)}"))

        taken = {"TP-100482", "TP-100517", "TP-100533", "TP-100391", "TP-100498", "TP-100215", "TP-100505",
                 "TP-100526", "TP-100300", "TP-100301", "TP-100302"}
        next_id = 100100
        for _ in range(160):
            while f"TP-{next_id}" in taken:
                next_id += 1
            oid = f"TP-{next_id}"
            next_id += 1
            c = random.choice(customers)
            age = random.uniform(0.05, 120)
            n_items = random.choices([1, 2, 3], [0.6, 0.3, 0.1])[0]
            pool = sellable if random.random() < 0.08 else small
            items = [(random.choice(pool), random.choices([1, 2], [0.85, 0.15])[0]) for _ in range(n_items)]
            make_order(s, oid, c, items, NOW - timedelta(days=age, minutes=random.randint(0, 600)))

        s.flush()
        # Pine Points: 1 point per £1 of non-refunded spend.
        for c in s.query(Customer).all():
            spent = sum(float(o.subtotal) for o in c.orders if o.status not in ("cancelled", "returned"))
            c.pine_points = int(spent)

    with session_scope() as s:
        counts = {st: s.query(Order).filter_by(status=st).count()
                  for st in ("processing", "dispatched", "delivered", "cancelled", "returned")}
        print(f"Seeded {s.query(Product).count()} products, {s.query(Customer).count()} customers, "
              f"{s.query(Order).count()} orders {counts}, {s.query(Refund).count()} refunds")
        print("DB:", get_settings().db_url.split("@")[-1])


if __name__ == "__main__":
    main()
