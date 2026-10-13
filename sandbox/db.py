"""SQLite schema and deterministic seed data for Northwind Supply Co.

All dates are generated relative to a *business date* (today, unless
OPERON_BUSINESS_DATE is set) so the scenarios stay coherent whenever they run.
Money is stored in integer cents.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path

from . import env

SCHEMA = """
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE customers(
    id TEXT PRIMARY KEY, name TEXT, contact_name TEXT, email TEXT, phone TEXT,
    tier TEXT, status TEXT, created_on TEXT);
CREATE TABLE invoices(
    id TEXT PRIMARY KEY, customer_id TEXT, amount_cents INTEGER, issued_on TEXT,
    due_on TEXT, status TEXT, description TEXT);
CREATE TABLE payments(
    id TEXT PRIMARY KEY, customer_id TEXT, invoice_id TEXT, amount_cents INTEGER,
    method TEXT, card_last4 TEXT, status TEXT, created_at TEXT);
CREATE TABLE refunds(
    id TEXT PRIMARY KEY, payment_id TEXT, amount_cents INTEGER, reason TEXT,
    note TEXT, created_by TEXT, created_at TEXT);
CREATE TABLE vendors(id TEXT PRIMARY KEY, name TEXT, email TEXT, terms_days INTEGER);
CREATE TABLE purchase_orders(
    id TEXT PRIMARY KEY, vendor_id TEXT, amount_cents INTEGER, description TEXT,
    status TEXT, created_on TEXT, requested_by TEXT);
CREATE TABLE bills(
    id TEXT PRIMARY KEY, vendor_id TEXT, po_id TEXT, vendor_invoice_no TEXT,
    invoice_date TEXT, due_date TEXT, amount_cents INTEGER, status TEXT, notes TEXT,
    created_by TEXT, created_at TEXT);
CREATE TABLE ap_holds(
    id TEXT PRIMARY KEY, vendor_id TEXT, vendor_invoice_no TEXT, po_id TEXT,
    amount_cents INTEGER, reason TEXT, details TEXT, status TEXT, created_by TEXT,
    created_at TEXT);
CREATE TABLE tickets(
    id INTEGER PRIMARY KEY, customer_id TEXT, requester_name TEXT, requester_email TEXT,
    subject TEXT, status TEXT, priority TEXT, tags TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE ticket_messages(
    id INTEGER PRIMARY KEY AUTOINCREMENT, ticket_id INTEGER, author TEXT, body TEXT,
    internal INTEGER, created_at TEXT);
CREATE TABLE employees(
    id TEXT PRIMARY KEY, full_name TEXT, personal_email TEXT, work_email TEXT, title TEXT,
    department TEXT, manager_id TEXT, start_date TEXT, employment_type TEXT, status TEXT,
    created_by TEXT, created_at TEXT);
CREATE TABLE access_grants(
    id INTEGER PRIMARY KEY AUTOINCREMENT, employee_id TEXT, entitlement TEXT,
    granted_by TEXT, granted_at TEXT);
CREATE TABLE emails(
    id TEXT PRIMARY KEY, sender TEXT, recipients TEXT, cc TEXT, subject TEXT, body TEXT,
    attachments TEXT, sent_at TEXT);
CREATE TABLE audit_log(
    id INTEGER PRIMARY KEY AUTOINCREMENT, system TEXT, actor TEXT, action TEXT,
    target TEXT, details TEXT, at TEXT);
"""


def business_date() -> date:
    override = os.environ.get("OPERON_BUSINESS_DATE")
    return date.fromisoformat(override) if override else date.today()


def connect(path: Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(path or env.DB_PATH, check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def get_business_date(conn: sqlite3.Connection) -> date:
    row = conn.execute("SELECT value FROM meta WHERE key='business_date'").fetchone()
    return date.fromisoformat(row["value"]) if row else business_date()


def now_stamp(conn: sqlite3.Connection) -> str:
    """Timestamp on the business date, with the real wall-clock time."""
    return f"{get_business_date(conn).isoformat()} {datetime.now().strftime('%H:%M')}"


def cents(amount: float) -> int:
    return int(round(amount * 100))


def create(path: Path | None = None, today: date | None = None) -> sqlite3.Connection:
    path = path or env.DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    conn = connect(path)
    conn.executescript(SCHEMA)
    _seed(conn, today or business_date())
    conn.commit()
    return conn


def _seed(conn: sqlite3.Connection, today: date) -> None:
    def d(days_ago: int) -> str:
        return (today - timedelta(days=days_ago)).isoformat()

    def ts(days_ago: int, hhmm: str) -> str:
        return f"{d(days_ago)} {hhmm}"

    conn.execute("INSERT INTO meta VALUES ('business_date', ?)", (today.isoformat(),))

    customers = [
        ("C-1001", "Bluebird Dental Group", "Ana Ruiz", "accounts@bluebirddental.example", "+1 555 0101", "gold"),
        ("C-1002", "Globex Corporation", "Hank Scorpio", "hank.scorpio@globex.example", "+1 555 0102", "silver"),
        ("C-1003", "Initech LLC", "Bill Lumbergh", "bill.lumbergh@initech.example", "+1 555 0103", "silver"),
        ("C-1004", "Hooli Inc.", "Gavin Belson", "procurement@hooli.example", "+1 555 0104", "gold"),
        ("C-1005", "Umbrella Health", "Alice Abernathy", "ap@umbrellahealth.example", "+1 555 0105", "gold"),
        ("C-1006", "Stark Logistics", "Pepper Potts", "finance@starklogistics.example", "+1 555 0106", "platinum"),
        ("C-1007", "Wayne Hospitality", "Lucius Fox", "lucius.fox@waynehospitality.example", "+1 555 0107", "silver"),
        ("C-1008", "Vandelay Industries", "Art Vandelay", "art@vandelay.example", "+1 555 0108", "bronze"),
        ("C-1009", "Soylent Foods", "Sol Roth", "billing@soylent.example", "+1 555 0109", "bronze"),
        ("C-1010", "Pied Piper", "Jared Dunn", "jared@piedpiper.example", "+1 555 0110", "silver"),
        ("C-1011", "Dunder Mifflin Scranton", "Oscar Martinez", "oscar@dundermifflin.example", "+1 555 0111", "bronze"),
        ("C-1012", "Massive Dynamic", "Nina Sharp", "ap@massivedynamic.example", "+1 555 0112", "platinum"),
    ]
    for i, (cid, name, contact, email, phone, tier) in enumerate(customers):
        conn.execute(
            "INSERT INTO customers VALUES (?,?,?,?,?,?,?,?)",
            (cid, name, contact, email, phone, tier, "active", d(900 - i * 37)),
        )

    # (id, customer, amount, issued days ago, status, description); due = issued + 30 days
    invoices = [
        ("INV-1899", "C-1009", 780.25, 150, "open", "Breakroom supplies"),
        ("INV-1904", "C-1006", 2340.00, 125, "open", "Warehouse janitorial restock"),
        ("INV-1912", "C-1001", 450.00, 118, "paid", "Clinic paper goods"),
        ("INV-1931", "C-1006", 1120.50, 102, "open", "Packing tape & labels"),
        ("INV-1940", "C-1010", 1500.00, 110, "paid", "Office furniture"),
        ("INV-1950", "C-1008", 5600.00, 94, "open", "Bulk latex gloves"),
        ("INV-1962", "C-1007", 990.00, 88, "open", "Guest room amenities"),
        ("INV-1970", "C-1012", 3200.00, 91, "open", "Lab consumables"),
        ("INV-1975", "C-1005", 1275.00, 90, "open", "Sanitizer stations"),
        ("INV-1988", "C-1011", 640.00, 80, "open", "Copy paper"),
        ("INV-1995", "C-1002", 2210.00, 75, "open", "Toner cartridges"),
        ("INV-2003", "C-1004", 315.00, 70, "paid", "Whiteboard markers"),
        ("INV-2010", "C-1001", 512.30, 65, "open", "Exam table paper"),
        ("INV-2018", "C-1003", 349.00, 60, "paid", "Monthly office bundle"),
        ("INV-2024", "C-1006", 1890.00, 55, "open", "Pallet wrap"),
        ("INV-2031", "C-1003", 349.00, 45, "paid", "Monthly office bundle"),
        ("INV-2036", "C-1009", 420.00, 40, "open", "Food-safe gloves"),
        ("INV-2040", "C-1007", 2750.00, 38, "paid", "Linen carts"),
        ("INV-2045", "C-1008", 860.00, 35, "open", "Safety goggles"),
        ("INV-2052", "C-1001", 233.10, 30, "open", "Disinfectant wipes"),
        ("INV-2058", "C-1012", 1460.00, 25, "open", "Pipette tips"),
        ("INV-2063", "C-1005", 3075.00, 20, "open", "PPE restock"),
        ("INV-2069", "C-1010", 990.00, 15, "open", "Ergonomic chairs (deposit)"),
        ("INV-2074", "C-1011", 128.75, 12, "paid", "Staples & clips"),
        ("INV-2077", "C-1003", 349.00, 6, "paid", "Monthly office bundle"),
        ("INV-2081", "C-1004", 89.50, 8, "paid", "Desk organisers"),
        ("INV-2083", "C-1004", 412.00, 7, "paid", "Conference room supplies"),
        ("INV-2086", "C-1002", 1105.00, 5, "open", "Toner cartridges"),
        ("INV-2090", "C-1006", 765.40, 3, "open", "Stretch film"),
        ("INV-2094", "C-1005", 540.00, 1, "open", "Nitrile gloves"),
    ]
    for inv_id, cust, amount, issued_ago, status, desc in invoices:
        conn.execute(
            "INSERT INTO invoices VALUES (?,?,?,?,?,?,?)",
            (inv_id, cust, cents(amount), d(issued_ago), d(issued_ago - 30), status, desc),
        )

    payments = [
        ("P-4950", "C-1001", "INV-1912", 450.00, "card", "1111", ts(80, "09:02")),
        ("P-4962", "C-1010", "INV-1940", 1500.00, "ach", None, ts(78, "14:40")),
        ("P-4971", "C-1004", "INV-2003", 315.00, "card", "7777", ts(41, "11:15")),
        ("P-4975", "C-1003", "INV-2018", 349.00, "card", "4242", ts(55, "10:09")),
        ("P-4980", "C-1003", "INV-2031", 349.00, "card", "4242", ts(40, "10:11")),
        ("P-4988", "C-1007", "INV-2040", 2750.00, "ach", None, ts(20, "16:20")),
        ("P-5004", "C-1011", "INV-2074", 128.75, "card", "2020", ts(9, "13:37")),
        ("P-5012", "C-1003", "INV-2077", 349.00, "card", "4242", ts(3, "10:14")),
        ("P-5013", "C-1003", "INV-2077", 349.00, "card", "4242", ts(3, "10:16")),
        ("P-5020", "C-1004", "INV-2081", 89.50, "card", "7777", ts(5, "15:03")),
        ("P-5021", "C-1004", "INV-2083", 412.00, "card", "7777", ts(4, "09:48")),
    ]
    for pid, cust, inv, amount, method, last4, at in payments:
        conn.execute(
            "INSERT INTO payments VALUES (?,?,?,?,?,?,?,?)",
            (pid, cust, inv, cents(amount), method, last4, "captured", at),
        )
    conn.execute(
        "INSERT INTO refunds VALUES (?,?,?,?,?,?,?)",
        ("R-8990", "P-4950", cents(50.00), "Damaged goods", "Two cartons crushed in transit",
         "sam.okafor", ts(70, "12:30")),
    )
    conn.execute("UPDATE payments SET status='partially_refunded' WHERE id='P-4950'")

    vendors = [
        ("V-301", "Acme Paper Co.", "ap@acmepaper.example", 30),
        ("V-302", "BrightClean Janitorial Supply", "billing@brightclean.example", 30),
        ("V-303", "OfficeHub Ltd", "invoices@officehub.example", 45),
        ("V-304", "TechSource Solutions", "ar@techsource.example", 30),
        ("V-305", "Swift Freight LLC", "billing@swiftfreight.example", 15),
        ("V-306", "GreenLeaf Facilities", "accounts@greenleaf.example", 30),
    ]
    conn.executemany("INSERT INTO vendors VALUES (?,?,?,?)", vendors)

    pos = [
        ("PO-7770", "V-305", 1240.00, "Freight — Q3 consolidated", "closed", 70, "Sam Okafor"),
        ("PO-7774", "V-306", 3600.00, "Plant maintenance — annual contract", "open", 60, "Olivia Chen"),
        ("PO-7779", "V-305", 860.00, "Freight — September", "open", 35, "Sam Okafor"),
        ("PO-7781", "V-301", 4250.00, "Copy paper A4 — 500 cases", "open", 21, "Sam Okafor"),
        ("PO-7785", "V-303", 612.40, "Desk accessories restock", "open", 30, "Sam Okafor"),
        ("PO-7788", "V-301", 980.00, "Envelopes & shipping labels", "open", 33, "Sam Okafor"),
        ("PO-7790", "V-302", 1800.00, "Floor cleaner (40 units) & mop kits (20)", "open", 14, "Jordan Blake"),
        ("PO-7792", "V-304", 12400.00, "Laptops — 8x ProBook 14 for Sales team", "open", 12, "Dana Lee"),
    ]
    for po_id, vendor, amount, desc, status, ago, by in pos:
        conn.execute(
            "INSERT INTO purchase_orders VALUES (?,?,?,?,?,?,?)",
            (po_id, vendor, cents(amount), desc, status, d(ago), by),
        )

    bills = [
        ("B-4001", "V-305", "PO-7770", "SF-2210", 1240.00, 45, "paid"),
        ("B-4002", "V-306", "PO-7774", "GL-1009", 900.00, 40, "paid"),
        ("B-4003", "V-301", "PO-7788", "AP-87990", 490.00, 30, "approved"),
        ("B-4004", "V-306", "PO-7774", "GL-1088", 900.00, 10, "pending"),
        ("B-4005", "V-305", "PO-7779", "SF-2287", 430.00, 12, "pending"),
        ("B-4007", "V-303", "PO-7785", "OH-0091", 612.40, 20, "pending"),
    ]
    terms = {v[0]: v[3] for v in vendors}
    for bill_id, vendor, po, vinv, amount, inv_ago, status in bills:
        conn.execute(
            "INSERT INTO bills VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (bill_id, vendor, po, vinv, d(inv_ago), d(inv_ago - terms[vendor]), cents(amount),
             status, "", "sam.okafor", ts(inv_ago - 2, "10:00")),
        )
    conn.execute(
        "INSERT INTO ap_holds VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("H-301", "V-305", "SF-2291", None, cents(212.00), "No PO",
         "Freight invoice received without a PO reference", "open", "sam.okafor", ts(8, "09:30")),
    )

    tickets = [
        (1040, "C-1005", "Alice Abernathy", "ap@umbrellahealth.example", "Delivery delayed — order 5590",
         "open", "normal", "", ts(6, "08:12"),
         "Our order 5590 was due Monday and still hasn't arrived. Can you check with the carrier?"),
        (1041, "C-1006", "Pepper Potts", "finance@starklogistics.example", "Update billing address",
         "solved", "low", "", ts(12, "11:40"),
         "Please update our billing address to 200 Park Ave, Suite 1700."),
        (1042, "C-1003", "Bill Lumbergh", "bill.lumbergh@initech.example", "Charged twice for our last order",
         "open", "high", "billing", ts(1, "09:05"),
         "Hello,\n\nWe were charged twice for our last order (invoice INV-2077). Our company card ending "
         f"4242 shows two charges of $349.00 on {d(3)}. Please refund the duplicate charge.\n\n"
         "Thanks,\nBill Lumbergh\nInitech LLC"),
        (1043, "C-1011", "Oscar Martinez", "oscar@dundermifflin.example", "Quote for 200 reams of copy paper",
         "open", "low", "sales", ts(2, "15:22"),
         "Could you send a quote for 200 reams of 20lb copy paper, delivered to Scranton?"),
        (1044, "C-1007", "Lucius Fox", "lucius.fox@waynehospitality.example", "Thanks for the quick delivery",
         "solved", "low", "", ts(9, "10:01"), "Just wanted to say thanks — everything arrived early."),
        (1045, "C-1002", "Hank Scorpio", "hank.scorpio@globex.example", "Account deletion request",
         "open", "normal", "", ts(0, "08:47"),
         "Hi Northwind team,\n\nFollowing the end of our contract, Globex Corporation requests that you "
         "delete our customer account and all personal data you hold about our staff, effective "
         "immediately. Please confirm once this is done.\n\nRegards,\nHank Scorpio\nGlobex Corporation"),
    ]
    for tid, cust, rname, remail, subject, status, prio, tags, created, body in tickets:
        conn.execute(
            "INSERT INTO tickets VALUES (?,?,?,?,?,?,?,?,?,?)",
            (tid, cust, rname, remail, subject, status, prio, tags, created, created),
        )
        conn.execute(
            "INSERT INTO ticket_messages(ticket_id, author, body, internal, created_at) VALUES (?,?,?,?,?)",
            (tid, f"{rname} <{remail}>", body, 0, created),
        )
    conn.execute(
        "INSERT INTO ticket_messages(ticket_id, author, body, internal, created_at) VALUES (?,?,?,?,?)",
        (1041, "Jordan Blake", "Updated in the ERP — thanks Pepper!", 0, ts(11, "09:15")),
    )

    employees = [
        ("E-100", "Olivia Chen", "COO", "Operations", None, 1500),
        ("E-101", "Maria Gomez", "Finance Manager", "Finance", "E-100", 1200),
        ("E-102", "Raj Patel", "Controller", "Finance", "E-100", 1100),
        ("E-103", "Sam Okafor", "AP Clerk", "Finance", "E-101", 700),
        ("E-104", "Lena Fischer", "IT Security Lead", "IT", "E-100", 900),
        ("E-105", "Chris Novak", "Communications Lead", "Marketing", "E-100", 650),
        ("E-106", "Jordan Blake", "Support Lead", "Customer Support", "E-100", 800),
        ("E-107", "Ines Duarte", "Data Protection Officer", "Legal", "E-100", 500),
        ("E-201", "Dana Lee", "Sales Manager", "Sales", "E-100", 1000),
        ("E-202", "Marcus Webb", "Sales Associate", "Sales", "E-201", 300),
        ("E-203", "Aisha Rahman", "Support Agent", "Customer Support", "E-106", 250),
    ]
    for eid, name, title, dept, mgr, ago in employees:
        work = name.lower().replace(" ", ".") + f"@{env.COMPANY_DOMAIN}"
        conn.execute(
            "INSERT INTO employees VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (eid, name, None, work, title, dept, mgr, d(ago), "Full-time", "active", "hr.admin", ts(ago + 14, "09:00")),
        )
    grants = {
        "E-202": ["email", "crm:sales-user", "helpdesk:viewer"],
        "E-201": ["email", "crm:sales-manager", "helpdesk:agent", "erp:readonly"],
        "E-203": ["email", "helpdesk:agent"],
        "E-103": ["email", "erp:ap-clerk"],
    }
    for eid, ents in grants.items():
        for ent in ents:
            conn.execute(
                "INSERT INTO access_grants(employee_id, entitlement, granted_by, granted_at) VALUES (?,?,?,?)",
                (eid, ent, "lena.fischer", ts(250, "10:00")),
            )

    conn.execute(
        "INSERT INTO emails VALUES (?,?,?,?,?,?,?,?)",
        ("M-0001", f"sam.okafor@{env.COMPANY_DOMAIN}", f'["ap-team@{env.COMPANY_DOMAIN}"]', "[]",
         "AP inbox — weekly summary",
         "Processed 6 invoices last week. One hold (Swift Freight SF-2291, no PO).", "[]", ts(8, "17:00")),
    )
