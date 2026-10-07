-- QuickBite sandbox world.
--
-- One SQLite file, but each table belongs to exactly one back-office system
-- (prefix support_/ops_/pay_) and each system's web app only reads and writes
-- its own tables. The systems are linked only by shared business identifiers
-- (customer id, order id), so the operator has to cross-reference them itself.
--
-- Money is stored in paise (integer) to avoid floating-point rounding.
-- Timestamps are ISO-8601 UTC strings.

-- ───────────────────────── Shared: staff accounts and sessions ─────────────────────────

CREATE TABLE staff_users (
    username      TEXT NOT NULL,
    system        TEXT NOT NULL CHECK (system IN ('support', 'ops', 'payments')),
    password      TEXT NOT NULL,
    display_name  TEXT NOT NULL,
    role          TEXT NOT NULL,
    PRIMARY KEY (username, system)
);

CREATE TABLE staff_sessions (
    token       TEXT PRIMARY KEY,
    username    TEXT NOT NULL,
    system      TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

-- ───────────────────────── Ops Admin ─────────────────────────

CREATE TABLE ops_customers (
    id          TEXT PRIMARY KEY,           -- CUST-1001
    name        TEXT NOT NULL,
    email       TEXT NOT NULL UNIQUE,
    phone       TEXT NOT NULL,
    city        TEXT NOT NULL,
    tier        TEXT NOT NULL,              -- regular / gold
    joined_at   TEXT NOT NULL
);

CREATE TABLE ops_restaurants (
    id          TEXT PRIMARY KEY,           -- REST-01
    name        TEXT NOT NULL,
    area        TEXT NOT NULL,
    rating      REAL NOT NULL,
    status      TEXT NOT NULL,              -- active / paused
    flagged     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE ops_riders (
    id          TEXT PRIMARY KEY,           -- RDR-201
    name        TEXT NOT NULL,
    phone       TEXT NOT NULL,
    vehicle     TEXT NOT NULL,
    rating      REAL NOT NULL
);

CREATE TABLE ops_orders (
    id              TEXT PRIMARY KEY,       -- QB-48213
    customer_id     TEXT NOT NULL REFERENCES ops_customers(id),
    restaurant_id   TEXT NOT NULL REFERENCES ops_restaurants(id),
    rider_id        TEXT REFERENCES ops_riders(id),
    status          TEXT NOT NULL CHECK (status IN
                      ('placed', 'accepted', 'preparing', 'picked_up', 'delivered', 'cancelled')),
    address         TEXT NOT NULL,
    placed_at       TEXT NOT NULL,
    promised_at     TEXT NOT NULL,
    delivered_at    TEXT,
    cancelled_at    TEXT,
    cancelled_by    TEXT,                   -- customer / restaurant / support
    cancel_reason   TEXT,
    subtotal        INTEGER NOT NULL,
    delivery_fee    INTEGER NOT NULL,
    taxes           INTEGER NOT NULL,
    total           INTEGER NOT NULL
);

CREATE TABLE ops_order_items (
    order_id    TEXT NOT NULL REFERENCES ops_orders(id),
    line_no     INTEGER NOT NULL,
    name        TEXT NOT NULL,
    qty         INTEGER NOT NULL,
    unit_price  INTEGER NOT NULL,
    PRIMARY KEY (order_id, line_no)
);

-- Delivery timeline: every status change and notable event.
CREATE TABLE ops_order_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id    TEXT NOT NULL REFERENCES ops_orders(id),
    at          TEXT NOT NULL,
    event       TEXT NOT NULL,
    note        TEXT
);

-- The restaurant's packing checklist, ticked when the bag is sealed.
CREATE TABLE ops_packing_log (
    order_id    TEXT NOT NULL REFERENCES ops_orders(id),
    line_no     INTEGER NOT NULL,
    item_name   TEXT NOT NULL,
    packed      INTEGER NOT NULL,
    checked_by  TEXT NOT NULL,
    PRIMARY KEY (order_id, line_no)
);

CREATE TABLE ops_incidents (
    id           TEXT PRIMARY KEY,          -- INC-5001
    target_type  TEXT NOT NULL CHECK (target_type IN ('restaurant', 'rider')),
    target_id    TEXT NOT NULL,
    order_id     TEXT,
    category     TEXT NOT NULL,
    description  TEXT NOT NULL,
    created_by   TEXT NOT NULL,
    created_at   TEXT NOT NULL
);

-- ───────────────────────── Payments Console ─────────────────────────

CREATE TABLE pay_payments (
    id           TEXT PRIMARY KEY,          -- PAY-90001
    order_id     TEXT NOT NULL,
    customer_id  TEXT NOT NULL,
    amount       INTEGER NOT NULL,
    method       TEXT NOT NULL,
    gateway_ref  TEXT NOT NULL,
    status       TEXT NOT NULL CHECK (status IN ('captured', 'partially_refunded', 'refunded')),
    captured_at  TEXT NOT NULL
);

CREATE TABLE pay_refunds (
    id                TEXT PRIMARY KEY,     -- RF-70001
    payment_id        TEXT NOT NULL REFERENCES pay_payments(id),
    order_id          TEXT NOT NULL,
    customer_id       TEXT NOT NULL,
    amount            INTEGER NOT NULL,
    reason_code       TEXT NOT NULL,
    note              TEXT NOT NULL DEFAULT '',
    status            TEXT NOT NULL CHECK (status IN ('processing', 'completed')),
    created_by        TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    expected_by       TEXT NOT NULL,
    completed_at      TEXT
);

CREATE TABLE pay_coupons (
    code         TEXT PRIMARY KEY,          -- QBSORRY-XXXXXX
    customer_id  TEXT NOT NULL,
    value        INTEGER NOT NULL,
    reason       TEXT NOT NULL,
    order_id     TEXT,
    created_by   TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    expires_at   TEXT NOT NULL,
    redeemed     INTEGER NOT NULL DEFAULT 0
);

-- ───────────────────────── Support Desk ─────────────────────────

CREATE TABLE support_tickets (
    id           TEXT PRIMARY KEY,          -- TKT-1001
    customer_id  TEXT NOT NULL,
    requester_name  TEXT NOT NULL,
    requester_email TEXT NOT NULL,
    order_id     TEXT,
    subject      TEXT NOT NULL,
    category     TEXT,                      -- set by whoever triages the ticket
    status       TEXT NOT NULL CHECK (status IN ('open', 'pending_customer', 'on_hold', 'resolved', 'closed')),
    priority     TEXT NOT NULL CHECK (priority IN ('low', 'normal', 'high', 'urgent')),
    channel      TEXT NOT NULL,             -- app / email / chat
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    linked_ticket_id TEXT
);

CREATE TABLE support_messages (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id    TEXT NOT NULL REFERENCES support_tickets(id),
    author_type  TEXT NOT NULL CHECK (author_type IN ('customer', 'agent', 'system')),
    author       TEXT NOT NULL,
    body         TEXT NOT NULL,
    internal     INTEGER NOT NULL DEFAULT 0,
    at           TEXT NOT NULL
);

CREATE TABLE support_attachments (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id     TEXT NOT NULL REFERENCES support_tickets(id),
    filename      TEXT NOT NULL,
    content_type  TEXT NOT NULL,
    data          BLOB NOT NULL,
    uploaded_at   TEXT NOT NULL
);

-- A scripted customer: when an agent posts a public reply to this ticket, the
-- customer answers with `body` after `delay_seconds`. Used for clarification flows.
CREATE TABLE support_scripted_replies (
    ticket_id      TEXT PRIMARY KEY REFERENCES support_tickets(id),
    body           TEXT NOT NULL,
    delay_seconds  INTEGER NOT NULL,
    due_at         TEXT,                    -- set when the agent replies
    delivered      INTEGER NOT NULL DEFAULT 0
);
