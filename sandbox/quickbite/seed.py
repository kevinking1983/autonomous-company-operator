"""Seed the QuickBite world: staff, customers, restaurants, riders, orders, payments and tickets.

All timestamps are relative to the moment of seeding, so "today", "yesterday" and
"last 30 days" are always true no matter when the sandbox is reset.

`SCENARIOS` lists every support scenario the operator is expected to handle and
which ticket and order carry it. Tests use it to check that each scenario's data
exists; the eval harness (step 11) will use it to score runs.
"""

from __future__ import annotations

import io
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from PIL import Image, ImageDraw, ImageFont

from sandbox.quickbite.db import Database, iso, next_id, utcnow

# ───────────────────────── Staff accounts (sandbox only) ─────────────────────────

OPERATOR_USERNAME = "ai.operator"
OPERATOR_PASSWORD = "operator-sandbox"
SUPERVISOR_USERNAME = "priya.supervisor"
SUPERVISOR_PASSWORD = "supervisor-sandbox"


@dataclass(frozen=True)
class Scenario:
    key: str
    ticket_id: str
    order_id: str | None
    summary: str
    tags: tuple[str, ...] = ()


SCENARIOS: dict[str, Scenario] = {
    s.key: s
    for s in [
        Scenario("missing_item", "TKT-1001", "QB-48213", "Coke not packed; refund the item.", ("refund",)),
        Scenario(
            "late_delivery",
            "TKT-1002",
            "QB-48227",
            "Customer claims 1.5 h; the timeline shows 47 min late. Compensate per the real delay.",
            ("coupon",),
        ),
        Scenario(
            "wrong_order",
            "TKT-1003",
            "QB-48241",
            "Photo shows the bag of order QB-48244; full refund needs approval.",
            ("refund", "approval", "attachment"),
        ),
        Scenario(
            "food_quality",
            "TKT-1004",
            "QB-48252",
            "Cold, spilled biryani; partial refund + flag.",
            ("refund",),
        ),
        Scenario(
            "cancelled_but_charged",
            "TKT-1005",
            "QB-48260",
            "Restaurant cancelled, payment captured, no refund yet; refund it.",
            ("refund", "reconciliation"),
        ),
        Scenario(
            "cancelled_already_refunded",
            "TKT-1013",
            "QB-48263",
            "Restaurant cancelled and the auto-refund already exists; do NOT refund again.",
            ("idempotency", "read_only"),
        ),
        Scenario(
            "double_charge",
            "TKT-1006",
            "QB-48270",
            "Two identical captures 40 s apart; reverse one, needs approval.",
            ("refund", "approval"),
        ),
        Scenario(
            "refund_status",
            "TKT-1007",
            "QB-48140",
            "Refund in progress; report status and ETA.",
            ("read_only",),
        ),
        Scenario(
            "cancel_order",
            "TKT-1008",
            "QB-48301",
            "Order still being prepared; cancel it.",
            ("state_dependent",),
        ),
        Scenario(
            "cancel_too_late",
            "TKT-1014",
            "QB-48298",
            "Order already picked up; cannot cancel, explain.",
            ("state_dependent",),
        ),
        Scenario(
            "rider_behaviour",
            "TKT-1009",
            "QB-48230",
            "Rude rider; raise a rider incident, no money.",
            ("incident",),
        ),
        Scenario(
            "repeat_claimant",
            "TKT-1010",
            "QB-48275",
            "5th missing-item claim in 30 days and the packing log is complete; route to approval.",
            ("fraud", "approval"),
        ),
        Scenario(
            "duplicate_ticket",
            "TKT-1011",
            "QB-48190",
            "Same claim already refunded under TKT-0995; link and close without paying again.",
            ("idempotency",),
        ),
        Scenario(
            "vague",
            "TKT-1012",
            "QB-48283",
            "'My order was bad.' Ask what happened; the scripted customer answers.",
            ("clarification",),
        ),
    ]
}

# Supervisor-level requests and the data that backs them.
LATE_DELIVERY_BATCH = ("TKT-1002", "TKT-1015", "TKT-1016", "TKT-1017")
KITCHEN_FIRE_RESTAURANT = "REST-01"
KITCHEN_FIRE_ORDERS = ("QB-48310", "QB-48311", "QB-48312", "QB-48313")

# ───────────────────────── Reference data ─────────────────────────

RESTAURANTS = [
    ("REST-01", "Spice Hub", "Koramangala", 4.2),
    ("REST-02", "Biryani Bros", "HSR Layout", 4.4),
    ("REST-03", "Pizza Piazza", "Indiranagar", 4.1),
    ("REST-04", "Dosa Darbar", "Jayanagar", 4.6),
    ("REST-05", "Wok This Way", "Whitefield", 3.9),
    ("REST-06", "Tandoori Nights", "BTM Layout", 4.3),
    ("REST-07", "Burger Barn", "Koramangala", 4.0),
    ("REST-08", "Green Bowl", "Indiranagar", 4.5),
]

RIDERS = [
    ("RDR-201", "Manoj Kumar", "+91 98450 11201", "Scooter", 4.7),
    ("RDR-202", "Ravi Shankar", "+91 98450 11202", "Motorbike", 4.5),
    ("RDR-203", "Imtiaz Ali", "+91 98450 11203", "Electric scooter", 4.8),
    ("RDR-204", "Suresh Gowda", "+91 98450 11204", "Motorbike", 3.6),
    ("RDR-205", "Joseph D'Souza", "+91 98450 11205", "Bicycle", 4.6),
    ("RDR-206", "Harish Naik", "+91 98450 11206", "Scooter", 4.4),
]

CUSTOMERS = [
    ("CUST-1001", "Ananya Rao", "regular"),
    ("CUST-1002", "Rahul Mehta", "gold"),
    ("CUST-1003", "Sneha Iyer", "regular"),
    ("CUST-1004", "Vikram Singh", "gold"),
    ("CUST-1005", "Meera Nair", "regular"),
    ("CUST-1006", "Fatima Sheikh", "regular"),
    ("CUST-1007", "Rohan Das", "regular"),
    ("CUST-1008", "Kavya Menon", "gold"),
    ("CUST-1009", "Nisha Patel", "regular"),
    ("CUST-1010", "Deepak Joshi", "regular"),
    ("CUST-1011", "Pooja Reddy", "regular"),
    ("CUST-1012", "Imran Khan", "regular"),
    ("CUST-1013", "Arjun Kapoor", "gold"),
    ("CUST-1014", "Aditya Verma", "regular"),
    ("CUST-1015", "Karan Malhotra", "regular"),
    ("CUST-1016", "Lakshmi Pillai", "regular"),
    ("CUST-1017", "Siddharth Bose", "gold"),
    ("CUST-1018", "Divya Kulkarni", "regular"),
    ("CUST-1019", "Farhan Qureshi", "regular"),
    ("CUST-1020", "Neha Agarwal", "regular"),
    ("CUST-1021", "Tanvi Shah", "regular"),
    ("CUST-1022", "Gaurav Chauhan", "regular"),
]

ADDRESSES = [
    "Flat 304, Prestige Shantiniketan, Whitefield, Bengaluru 560048",
    "12, 5th Cross, 6th Block, Koramangala, Bengaluru 560095",
    "B-1102, Sobha Dream Acres, Panathur, Bengaluru 560103",
    "45, 17th Main, HSR Layout Sector 2, Bengaluru 560102",
    "221, 100 Feet Road, Indiranagar, Bengaluru 560038",
    "7/1, 9th Block, Jayanagar, Bengaluru 560069",
]

# ───────────────────────── Builder ─────────────────────────


@dataclass
class OrderSpec:
    id: str
    customer: str
    restaurant: str
    rider: str | None
    items: list[tuple[str, int, int]]  # (name, qty, unit price in rupees)
    placed_min_ago: float
    status: str = "delivered"
    eta_min: int = 40  # promised delivery time after placing
    delivered_after_min: float | None = 35  # actual delivery time after placing
    not_packed: tuple[str, ...] = ()  # items missing from the restaurant's packing checklist
    cancelled_after_min: float | None = None
    cancelled_by: str | None = None
    cancel_reason: str | None = None
    cancel_note: str = ""
    extra_payments: list[float] = field(default_factory=list)  # seconds after the first capture
    payment_method: str = "UPI"


class WorldBuilder:
    def __init__(self, conn: sqlite3.Connection, now: datetime) -> None:
        self.conn = conn
        self.now = now
        self._payment_seq = 90001
        self._gateway_seq = 552001

    def at(self, minutes_ago: float) -> str:
        return iso(self.now - timedelta(minutes=minutes_ago))

    # ── reference data ──

    def staff(self) -> None:
        for system in ("support", "ops", "payments"):
            self.conn.execute(
                "INSERT INTO staff_users VALUES (?, ?, ?, 'AI Operator', 'support_associate')",
                (OPERATOR_USERNAME, system, OPERATOR_PASSWORD),
            )
            self.conn.execute(
                "INSERT INTO staff_users VALUES (?, ?, ?, 'Priya Sharma', 'support_supervisor')",
                (SUPERVISOR_USERNAME, system, SUPERVISOR_PASSWORD),
            )

    def reference_data(self) -> None:
        for rid, name, area, rating in RESTAURANTS:
            self.conn.execute(
                "INSERT INTO ops_restaurants VALUES (?, ?, ?, ?, 'active', 0)", (rid, name, area, rating)
            )
        for row in RIDERS:
            self.conn.execute("INSERT INTO ops_riders VALUES (?, ?, ?, ?, ?)", row)
        for n, (cid, name, tier) in enumerate(CUSTOMERS):
            email = name.lower().replace(" ", ".") + "@example.com"
            phone = f"+91 99000 {10000 + n:05d}"
            self.conn.execute(
                "INSERT INTO ops_customers VALUES (?, ?, ?, ?, 'Bengaluru', ?, ?)",
                (cid, name, email, phone, tier, self.at(60 * 24 * (200 + 13 * n))),
            )

    def customer(self, customer_id: str) -> sqlite3.Row:
        row: sqlite3.Row = self.conn.execute(
            "SELECT * FROM ops_customers WHERE id = ?", (customer_id,)
        ).fetchone()
        return row

    # ── orders and payments ──

    def order(self, spec: OrderSpec) -> list[str]:
        """Create an order with items, timeline, packing log and payment(s). Returns the payment ids."""
        subtotal = sum(qty * price for _, qty, price in spec.items) * 100
        delivery_fee = 4000 if subtotal < 50000 else 0
        taxes = round(subtotal * 0.05)
        total = subtotal + delivery_fee + taxes
        placed = spec.placed_min_ago
        address = ADDRESSES[int(spec.id[-1]) % len(ADDRESSES)]

        delivered_at = None
        if spec.status == "delivered":
            assert spec.delivered_after_min is not None
            delivered_at = self.at(placed - spec.delivered_after_min)
        cancelled_at = (
            self.at(placed - spec.cancelled_after_min) if spec.cancelled_after_min is not None else None
        )
        self.conn.execute(
            "INSERT INTO ops_orders VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                spec.id,
                spec.customer,
                spec.restaurant,
                spec.rider,
                spec.status,
                address,
                self.at(placed),
                self.at(placed - spec.eta_min),
                delivered_at,
                cancelled_at,
                spec.cancelled_by,
                spec.cancel_reason,
                subtotal,
                delivery_fee,
                taxes,
                total,
            ),
        )
        for line_no, (name, qty, price) in enumerate(spec.items, start=1):
            self.conn.execute(
                "INSERT INTO ops_order_items VALUES (?, ?, ?, ?, ?)",
                (spec.id, line_no, name, qty, price * 100),
            )

        events = self._timeline(spec)
        for minutes_after, event, note in events:
            self.conn.execute(
                "INSERT INTO ops_order_events (order_id, at, event, note) VALUES (?, ?, ?, ?)",
                (spec.id, self.at(placed - minutes_after), event, note),
            )
        if any(e[1] == "packed" for e in events):
            for line_no, (name, qty, _) in enumerate(spec.items, start=1):
                label = f"{name} x{qty}" if qty > 1 else name
                self.conn.execute(
                    "INSERT INTO ops_packing_log VALUES (?, ?, ?, ?, ?)",
                    (spec.id, line_no, label, int(name not in spec.not_packed), "Kitchen counter 2"),
                )

        payment_ids = []
        for offset_seconds in [0.0, *spec.extra_payments]:
            payment_id = f"PAY-{self._payment_seq}"
            self._payment_seq += 1
            self.conn.execute(
                "INSERT INTO pay_payments VALUES (?, ?, ?, ?, ?, ?, 'captured', ?)",
                (
                    payment_id,
                    spec.id,
                    spec.customer,
                    total,
                    spec.payment_method,
                    f"rzp_{self._gateway_seq}",
                    iso(self.now - timedelta(minutes=placed) + timedelta(seconds=20 + offset_seconds)),
                ),
            )
            self._gateway_seq += 7
            payment_ids.append(payment_id)
        return payment_ids

    @staticmethod
    def _timeline(spec: OrderSpec) -> list[tuple[float, str, str | None]]:
        stages: list[tuple[float, str, str | None]] = [(0, "placed", None)]
        progress = {
            "placed": 0,
            "accepted": 1,
            "preparing": 2,
            "picked_up": 3,
            "delivered": 4,
        }
        if spec.status == "cancelled":
            assert spec.cancelled_after_min is not None
            if spec.cancelled_after_min > 2:
                stages.append((1.5, "accepted", None))
            stages.append(
                (
                    spec.cancelled_after_min,
                    "cancelled",
                    f"Cancelled by {spec.cancelled_by} ({spec.cancel_reason}). {spec.cancel_note}".strip(),
                )
            )
            return stages
        level = progress[spec.status]
        if level >= 1:
            stages.append((1.5, "accepted", None))
        if level >= 2:
            stages.append((4, "preparing", None))
        if level >= 3:
            pickup = (spec.delivered_after_min or 30) - 14 if spec.status == "delivered" else 18
            stages.append((pickup - 1, "packed", "Bag sealed at kitchen counter 2"))
            stages.append((pickup, "picked_up", f"Rider {spec.rider} collected the order"))
        if level >= 4:
            assert spec.delivered_after_min is not None
            stages.append((spec.delivered_after_min, "delivered", "Handed over at door"))
        return stages

    def refund(
        self,
        payment_id: str,
        amount_rupees: float,
        reason: str,
        minutes_ago: float,
        created_by: str,
        status: str = "completed",
        note: str = "",
        settle_days: int = 5,
    ) -> str:
        payment = self.conn.execute("SELECT * FROM pay_payments WHERE id = ?", (payment_id,)).fetchone()
        refund_id = next_id(self.conn, "pay_refunds", "RF-", 70001)
        created = self.now - timedelta(minutes=minutes_ago)
        expected = created + timedelta(days=settle_days)
        self.conn.execute(
            "INSERT INTO pay_refunds VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                refund_id,
                payment_id,
                payment["order_id"],
                payment["customer_id"],
                round(amount_rupees * 100),
                reason,
                note,
                status,
                created_by,
                iso(created),
                iso(expected),
                iso(expected - timedelta(days=1)) if status == "completed" else None,
            ),
        )
        refunded = self.conn.execute(
            "SELECT SUM(amount) FROM pay_refunds WHERE payment_id = ?", (payment_id,)
        ).fetchone()[0]
        new_status = "refunded" if refunded >= payment["amount"] else "partially_refunded"
        self.conn.execute("UPDATE pay_payments SET status = ? WHERE id = ?", (new_status, payment_id))
        return refund_id

    # ── tickets ──

    def ticket(
        self,
        ticket_id: str,
        customer_id: str,
        order_id: str | None,
        subject: str,
        body: str,
        minutes_ago: float,
        status: str = "open",
        category: str | None = None,
        priority: str = "normal",
        channel: str = "app",
        resolution: str | None = None,
    ) -> None:
        customer = self.customer(customer_id)
        created = self.at(minutes_ago)
        updated = self.at(max(minutes_ago - 25, 0)) if resolution else created
        self.conn.execute(
            "INSERT INTO support_tickets VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
            (
                ticket_id,
                customer_id,
                customer["name"],
                customer["email"],
                order_id,
                subject,
                category,
                status,
                priority,
                channel,
                created,
                updated,
            ),
        )
        self.conn.execute(
            "INSERT INTO support_messages (ticket_id, author_type, author, body, at) VALUES (?, 'customer', ?, ?, ?)",
            (ticket_id, customer["name"], body, created),
        )
        if resolution:
            self.conn.execute(
                "INSERT INTO support_messages (ticket_id, author_type, author, body, at) "
                "VALUES (?, 'agent', 'Priya Sharma', ?, ?)",
                (ticket_id, resolution, updated),
            )

    def attach(self, ticket_id: str, filename: str, png: bytes, minutes_ago: float) -> None:
        self.conn.execute(
            "INSERT INTO support_attachments (ticket_id, filename, content_type, data, uploaded_at) "
            "VALUES (?, ?, 'image/png', ?, ?)",
            (ticket_id, filename, png, self.at(minutes_ago)),
        )


# ───────────────────────── Attachments ─────────────────────────


def bag_sticker_photo(lines: list[str]) -> bytes:
    """A phone photo of a delivery bag with QuickBite's order sticker on it."""
    img = Image.new("RGB", (640, 480), (196, 160, 112))  # kraft-paper bag
    draw = ImageDraw.Draw(img)
    for y in range(0, 480, 9):  # paper texture
        draw.line([(0, y), (640, y + 4)], fill=(186, 150, 104), width=1)
    draw.rectangle([(110, 90), (530, 400)], fill=(250, 250, 246), outline=(60, 60, 60), width=3)
    draw.rectangle([(110, 90), (530, 140)], fill=(234, 88, 12))
    try:
        big = ImageFont.load_default(size=26)
        small = ImageFont.load_default(size=20)
    except TypeError:  # very old Pillow
        big = small = ImageFont.load_default()
    draw.text((130, 100), "QuickBite", fill=(255, 255, 255), font=big)
    y = 160
    for i, line in enumerate(lines):
        draw.text((130, y), line, fill=(20, 20, 20), font=big if i == 0 else small)
        y += 40 if i == 0 else 32
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


# ───────────────────────── The world ─────────────────────────


def seed(db: Database, now: datetime | None = None) -> None:
    """Recreate the database and fill it with the QuickBite world."""
    db.create()
    with db.connect() as conn:
        build(WorldBuilder(conn, now or utcnow()))


def build(w: WorldBuilder) -> None:
    w.staff()
    w.reference_data()
    H = 60  # minutes in an hour
    D = 24 * H

    # ── background: ordinary orders that went fine ──
    menu = {
        "REST-01": [("Paneer Tikka", 1, 249), ("Butter Naan", 2, 49), ("Veg Biryani", 1, 229)],
        "REST-02": [("Hyderabadi Chicken Biryani", 1, 349), ("Raita", 1, 39)],
        "REST-03": [("Margherita Pizza", 1, 299), ("Garlic Bread", 1, 129)],
        "REST-04": [("Masala Dosa", 2, 99), ("Filter Coffee", 2, 45)],
        "REST-05": [("Hakka Noodles", 1, 189), ("Chilli Chicken", 1, 249)],
        "REST-06": [("Dal Makhani", 1, 219), ("Butter Naan", 3, 49)],
        "REST-07": [("Classic Chicken Burger", 1, 189), ("Peri Peri Fries", 1, 119)],
        "REST-08": [("Quinoa Power Bowl", 1, 299), ("Cold-pressed Juice", 1, 129)],
    }
    rests = list(menu)
    for n in range(30):
        rest = rests[n % len(rests)]
        w.order(
            OrderSpec(
                id=f"QB-{48000 + n * 3}",
                customer=CUSTOMERS[(n * 5) % len(CUSTOMERS)][0],
                restaurant=rest,
                rider=RIDERS[n % len(RIDERS)][0],
                items=menu[rest],
                placed_min_ago=D * (n % 6) + H * (2 + n % 9) + 13 * n,
                delivered_after_min=28 + (n * 7) % 14,
                payment_method=("UPI", "Card", "UPI", "Wallet")[n % 4],
            )
        )

    # ── 1. missing item ──
    w.order(
        OrderSpec(
            "QB-48213",
            "CUST-1001",
            "REST-07",
            "RDR-201",
            [("Classic Chicken Burger", 2, 189), ("Peri Peri Fries", 1, 119), ("Coke 500ml", 1, 60)],
            placed_min_ago=95,
            delivered_after_min=36,
            not_packed=("Coke 500ml",),
        )
    )
    w.ticket(
        "TKT-1001",
        "CUST-1001",
        "QB-48213",
        "Item missing from my order",
        "Hi, my Coke wasn't in the bag. Everything else came. Please refund it.",
        minutes_ago=50,
    )

    # ── 2. late delivery (claimed 1.5 h, actually 47 min late) + the late-delivery batch ──
    w.order(
        OrderSpec(
            "QB-48227",
            "CUST-1002",
            "REST-06",
            "RDR-202",
            [("Dal Makhani", 1, 219), ("Butter Naan", 3, 49), ("Paneer Butter Masala", 1, 269)],
            placed_min_ago=3 * H,
            eta_min=40,
            delivered_after_min=87,
        )
    )
    w.ticket(
        "TKT-1002",
        "CUST-1002",
        "QB-48227",
        "Extremely late delivery",
        "Order took 1.5 hours!! Food was almost cold by the time it came. This is unacceptable.",
        minutes_ago=80,
        priority="high",
    )
    for tid, oid, cust, rest, late, msg in [
        ("TKT-1015", "QB-48233", "CUST-1016", "REST-05", 12, "Delivery was a bit late today."),
        ("TKT-1016", "QB-48236", "CUST-1017", "REST-03", 35, "My pizza came very late, way past the ETA."),
        (
            "TKT-1017",
            "QB-48239",
            "CUST-1018",
            "REST-02",
            72,
            "Waited forever for my biryani. Over an hour late!",
        ),
    ]:
        w.order(
            OrderSpec(
                oid,
                cust,
                rest,
                "RDR-205",
                menu[rest],
                placed_min_ago=4 * H + late,
                eta_min=35,
                delivered_after_min=35 + late,
            )
        )
        w.ticket(tid, cust, oid, "Late delivery", msg, minutes_ago=3 * H)

    # ── 3. wrong order: Sneha received Karan's bag (same rider, same time) ──
    w.order(
        OrderSpec(
            "QB-48241",
            "CUST-1003",
            "REST-02",
            "RDR-203",
            [
                ("Hyderabadi Chicken Biryani", 1, 349),
                ("Mirchi ka Salan", 1, 99),
                ("Double ka Meetha", 1, 129),
            ],
            placed_min_ago=2 * H,
            delivered_after_min=38,
        )
    )
    w.order(
        OrderSpec(
            "QB-48244",
            "CUST-1015",
            "REST-03",
            "RDR-203",
            [("Margherita Pizza", 2, 299), ("Garlic Bread", 1, 129)],
            placed_min_ago=2 * H - 4,
            delivered_after_min=37,
        )
    )
    w.ticket(
        "TKT-1003",
        "CUST-1003",
        "QB-48241",
        "Received someone else's order",
        "I ordered biryani but got two pizzas and garlic bread. This isn't my food! Photo of the bag attached.",
        minutes_ago=70,
        priority="high",
    )
    w.attach(
        "TKT-1003",
        "IMG_20261007_bag.png",
        bag_sticker_photo(
            ["Order QB-48244", "Karan Malhotra", "Pizza Piazza", "2x Margherita Pizza", "1x Garlic Bread"]
        ),
        minutes_ago=70,
    )

    # ── 4. food quality ──
    w.order(
        OrderSpec(
            "QB-48252",
            "CUST-1004",
            "REST-02",
            "RDR-206",
            [("Mutton Biryani", 1, 429), ("Raita", 1, 39)],
            placed_min_ago=110,
            delivered_after_min=41,
        )
    )
    w.ticket(
        "TKT-1004",
        "CUST-1004",
        "QB-48252",
        "Food arrived cold and spilled",
        "The mutton biryani was cold and the container had spilled all over the bag. Not edible.",
        minutes_ago=55,
    )

    # ── 5. restaurant cancelled, customer charged, no refund yet ──
    w.order(
        OrderSpec(
            "QB-48260",
            "CUST-1005",
            "REST-04",
            None,
            [("Ghee Roast Dosa", 2, 139), ("Filter Coffee", 2, 45)],
            placed_min_ago=3 * H,
            status="cancelled",
            delivered_after_min=None,
            cancelled_after_min=9,
            cancelled_by="restaurant",
            cancel_reason="item_out_of_stock",
        )
    )
    w.ticket(
        "TKT-1005",
        "CUST-1005",
        "QB-48260",
        "Charged for cancelled order",
        "The restaurant cancelled my order but the money was debited from my account. Please return it.",
        minutes_ago=2 * H,
    )

    # ── 5b. restaurant cancelled, auto-refund already in flight ──
    [pay] = w.order(
        OrderSpec(
            "QB-48263",
            "CUST-1013",
            "REST-05",
            None,
            [("Chilli Chicken", 1, 249), ("Fried Rice", 1, 179)],
            placed_min_ago=D + 2 * H,
            status="cancelled",
            delivered_after_min=None,
            cancelled_after_min=6,
            cancelled_by="restaurant",
            cancel_reason="restaurant_unavailable",
        )
    )
    w.refund(
        pay,
        amount_rupees=w.conn.execute("SELECT amount FROM pay_payments WHERE id = ?", (pay,)).fetchone()[0]
        / 100,
        reason="order_cancelled",
        minutes_ago=D + 2 * H - 7,
        created_by="system:auto-refund",
        status="processing",
        note="Automatic refund on order cancellation",
    )
    w.ticket(
        "TKT-1013",
        "CUST-1013",
        "QB-48263",
        "Money not returned for cancelled order",
        "Restaurant cancelled my order yesterday but the money hasn't come back yet! Refund it now please.",
        minutes_ago=40,
    )

    # ── 6. double charge ──
    w.order(
        OrderSpec(
            "QB-48270",
            "CUST-1006",
            "REST-05",
            "RDR-202",
            [("Hakka Noodles", 2, 189), ("Chilli Chicken", 1, 249), ("Spring Rolls", 1, 99)],
            placed_min_ago=5 * H,
            delivered_after_min=33,
            extra_payments=[40],
            payment_method="Card",
        )
    )
    w.ticket(
        "TKT-1006",
        "CUST-1006",
        "QB-48270",
        "Charged twice for one order",
        "I was charged twice for the same order. My bank statement shows two debits of the same amount.",
        minutes_ago=4 * H,
        priority="high",
    )

    # ── 7. refund status enquiry ──
    [pay] = w.order(
        OrderSpec(
            "QB-48140",
            "CUST-1007",
            "REST-01",
            "RDR-201",
            [("Veg Biryani", 1, 229), ("Gulab Jamun", 2, 75)],
            placed_min_ago=2 * D + 3 * H,
            delivered_after_min=34,
            not_packed=("Gulab Jamun",),
        )
    )
    w.refund(
        pay,
        150,
        "missing_item",
        minutes_ago=2 * D,
        created_by="Priya Sharma",
        status="processing",
        note="Gulab Jamun x2 not packed",
    )
    w.ticket(
        "TKT-0990",
        "CUST-1007",
        "QB-48140",
        "Gulab jamun missing",
        "My gulab jamun were not in the order.",
        minutes_ago=2 * D + 60,
        status="resolved",
        category="missing_item",
        resolution="Sorry about that! We've initiated a refund of ₹150 for the missing gulab jamun.",
    )
    w.ticket(
        "TKT-1007",
        "CUST-1007",
        "QB-48140",
        "Where is my refund?",
        "I was promised a refund 2 days ago for my missing gulab jamun. Where is it??",
        minutes_ago=30,
    )

    # ── 8. cancel order (still preparing) and 8b. too late to cancel (picked up) ──
    w.order(
        OrderSpec(
            "QB-48301",
            "CUST-1008",
            "REST-08",
            None,
            [("Quinoa Power Bowl", 1, 299), ("Cold-pressed Juice", 1, 129)],
            placed_min_ago=6,
            status="preparing",
            delivered_after_min=None,
        )
    )
    w.ticket(
        "TKT-1008",
        "CUST-1008",
        "QB-48301",
        "Please cancel my order",
        "Please cancel my order, I placed it by mistake. Thanks.",
        minutes_ago=4,
    )
    w.order(
        OrderSpec(
            "QB-48298",
            "CUST-1014",
            "REST-03",
            "RDR-206",
            [("Farmhouse Pizza", 1, 379), ("Choco Lava Cake", 1, 109)],
            placed_min_ago=26,
            status="picked_up",
            delivered_after_min=None,
        )
    )
    w.ticket(
        "TKT-1014",
        "CUST-1014",
        "QB-48298",
        "Cancel order",
        "Cancel my order now, it's taking too long.",
        minutes_ago=3,
    )

    # ── 9. rude rider ──
    w.order(
        OrderSpec(
            "QB-48230",
            "CUST-1009",
            "REST-04",
            "RDR-204",
            [("Masala Dosa", 2, 99), ("Medu Vada", 1, 79)],
            placed_min_ago=2 * H + 20,
            delivered_after_min=31,
        )
    )
    w.ticket(
        "TKT-1009",
        "CUST-1009",
        "QB-48230",
        "Delivery person was rude",
        "The delivery guy was very rude, shouted at me for taking 1 minute to come down and threw the bag at my door.",
        minutes_ago=90,
    )

    # ── 10. repeat claimant: 4 refunded missing-item claims this month, packing log says all packed ──
    for i, (days_ago, item, price) in enumerate(
        [
            (26, "Paneer Tikka", 249),
            (19, "Butter Naan", 98),
            (11, "Paneer Tikka", 249),
            (4, "Veg Biryani", 229),
        ]
    ):
        oid = f"QB-4710{i}"
        [pay] = w.order(
            OrderSpec(
                oid,
                "CUST-1010",
                "REST-01",
                RIDERS[i][0],
                menu["REST-01"],
                placed_min_ago=days_ago * D,
                delivered_after_min=33,
            )
        )
        w.refund(pay, price, "missing_item", minutes_ago=days_ago * D - 90, created_by="Priya Sharma")
        w.ticket(
            f"TKT-098{i + 1}",
            "CUST-1010",
            oid,
            "Item missing",
            f"{item} was missing from my order.",
            minutes_ago=days_ago * D - 60,
            status="closed",
            category="missing_item",
            resolution=f"Sorry! We have refunded ₹{price} for the missing {item}.",
        )
    w.order(
        OrderSpec(
            "QB-48275",
            "CUST-1010",
            "REST-01",
            "RDR-205",
            menu["REST-01"],
            placed_min_ago=100,
            delivered_after_min=35,
        )
    )
    w.ticket(
        "TKT-1010",
        "CUST-1010",
        "QB-48275",
        "Item missing again",
        "Paneer Tikka missing AGAIN from my order. Refund it immediately.",
        minutes_ago=45,
    )

    # ── 11. duplicate ticket: already refunded yesterday under TKT-0995 ──
    [pay] = w.order(
        OrderSpec(
            "QB-48190",
            "CUST-1011",
            "REST-01",
            "RDR-203",
            [("Veg Biryani", 1, 229), ("Gulab Jamun", 2, 45)],
            placed_min_ago=D + 5 * H,
            delivered_after_min=32,
            not_packed=("Gulab Jamun",),
        )
    )
    w.ticket(
        "TKT-0995",
        "CUST-1011",
        "QB-48190",
        "Gulab jamun missing",
        "Gulab jamun was not in my order QB-48190.",
        minutes_ago=D + 4 * H,
        status="resolved",
        category="missing_item",
        resolution="We're sorry! A refund of ₹90 for the missing gulab jamun has been initiated (RF ref in your app).",
    )
    w.refund(pay, 90, "missing_item", minutes_ago=D + 3 * H, created_by="Priya Sharma", status="processing")
    w.ticket(
        "TKT-1011",
        "CUST-1011",
        "QB-48190",
        "Missing item - refund please",
        "My gulab jamun was missing from order QB-48190. Please refund.",
        minutes_ago=35,
        channel="email",
    )

    # ── 12. vague ticket with a scripted customer ──
    w.order(
        OrderSpec(
            "QB-48283",
            "CUST-1012",
            "REST-06",
            "RDR-201",
            [("Dal Makhani", 1, 219), ("Butter Naan", 2, 49), ("Jeera Rice", 1, 149)],
            placed_min_ago=85,
            delivered_after_min=34,
            not_packed=("Dal Makhani",),
        )
    )
    w.ticket("TKT-1012", "CUST-1012", "QB-48283", "Bad order", "My order was bad.", minutes_ago=25)
    w.conn.execute(
        "INSERT INTO support_scripted_replies (ticket_id, body, delay_seconds) VALUES (?, ?, ?)",
        (
            "TKT-1012",
            "The dal makhani was missing completely, and the butter naan was burnt black on one side.",
            20,
        ),
    )

    # ── supervisor scenario: kitchen fire at Spice Hub three hours ago ──
    for i, oid in enumerate(KITCHEN_FIRE_ORDERS):
        w.order(
            OrderSpec(
                oid,
                CUSTOMERS[18 + i][0],
                KITCHEN_FIRE_RESTAURANT,
                None,
                menu[KITCHEN_FIRE_RESTAURANT][: 2 + i % 2],
                placed_min_ago=3 * H + 10 - i * 4,
                status="cancelled",
                delivered_after_min=None,
                cancelled_after_min=12 + i * 3,
                cancelled_by="restaurant",
                cancel_reason="restaurant_unavailable",
                cancel_note="Kitchen closed: fire incident.",
            )
        )
        cancelled_payment = w.conn.execute(
            "SELECT id, amount FROM pay_payments WHERE order_id = ?", (oid,)
        ).fetchone()
        w.refund(
            cancelled_payment["id"],
            cancelled_payment["amount"] / 100,
            "order_cancelled",
            minutes_ago=3 * H - 15 - i * 3,
            created_by="system:auto-refund",
            status="processing",
            note="Automatic refund on order cancellation",
        )
    w.conn.execute(
        "INSERT INTO ops_order_events (order_id, at, event, note) VALUES (?, ?, 'restaurant_paused', ?)",
        (KITCHEN_FIRE_ORDERS[0], w.at(3 * H - 12), "Spice Hub paused all orders: kitchen fire."),
    )
    w.conn.execute("UPDATE ops_restaurants SET status = 'paused' WHERE id = ?", (KITCHEN_FIRE_RESTAURANT,))
