# TurnStack — Developer Documentation

> **The WhatsApp conversation engine that gets out of your way.**
> You define the flow. TurnStack drives it.

---

## Table of Contents

1. [What TurnStack Is (and Isn't)](#1-what-turnstack-is-and-isnt)
2. [Core Concepts](#2-core-concepts)
3. [Quick Start](#3-quick-start)
4. [The Flow Tree](#4-the-flow-tree)
5. [Building Blocks — Node Reference](#5-building-blocks--node-reference)
   - [Menu](#51-menu)
   - [Input](#52-input)
   - [Confirm](#53-confirm)
   - [Action](#54-action)
   - [Router](#55-router)
   - [ListNode](#56-listnode)
   - [MediaReply](#57-mediareply)
6. [Field Types (inside Input)](#6-field-types-inside-input)
   - [Field / TextField](#61-field--textfield)
   - [MenuField](#62-menufield)
   - [ButtonsField](#63-buttonsfield)
   - [ImageField](#64-imagefield)
   - [DocumentField](#65-documentfield)
   - [LocationField](#66-locationfield)
   - [BranchField](#67-branchfield)
7. [The Engine](#7-the-engine)
   - [Instantiation](#71-instantiation)
   - [process()](#72-process)
   - [IncomingMessage](#73-incomingmessage)
   - [Reply](#74-reply)
8. [Session & State](#8-session--state)
   - [Session object](#81-session-object)
   - [session.collected](#82-sessioncollected)
   - [session.context](#83-sessioncontext)
   - [session.pagination](#84-sessionpagination)
9. [Session Stores](#9-session-stores)
   - [InMemorySessionStore](#91-inmemorysessionstore)
   - [Custom stores](#92-custom-stores)
10. [Navigation — Built-in Commands](#10-navigation--built-in-commands)
11. [Sending Replies — Adapter Pattern](#11-sending-replies--adapter-pattern)
    - [Reading reply fields](#111-reading-reply-fields)
    - [Sending via REST](#112-sending-via-rest)
    - [Sending via pywa / any library](#113-sending-via-pywa--any-library)
12. [Wiring to a Webhook](#12-wiring-to-a-webhook)
13. [Validation & Transformation](#13-validation--transformation)
14. [Dynamic Content](#14-dynamic-content)
15. [Conditional Fields — BranchField](#15-conditional-fields--branchfield)
16. [Pagination — Automatic Behaviour](#16-pagination--automatic-behaviour)
17. [Custom Node Handlers](#17-custom-node-handlers)
18. [Error Handling](#18-error-handling)
19. [Debug Utilities](#19-debug-utilities)
20. [Complete Example — Customer Support Bot](#20-complete-example--customer-support-bot)

---

## 1. What TurnStack Is (and Isn't)

**TurnStack is a conversation-flow engine.** You give it a tree of nodes. It receives raw WhatsApp messages, drives the user through the tree, manages all session state, and hands you back structured `Reply` objects ready to send.

**What TurnStack handles for you:**

- Session lifecycle (create, persist, expire, reset)
- Navigation state machine (current node, history stack, back/home/exit)
- Multi-step form collection with per-field validation
- Menu and list pagination (automatic, configurable)
- Interactive vs plain-text fallback rendering hints
- Unsupported message types (stickers, audio, reactions) — polite reply, no state change
- Media file delivery followed by the next node — both sent automatically
- Global navigation commands (`back`, `home`, `exit`) intercepted before dispatch

**What TurnStack does NOT do:**

- Send messages — that's your adapter (REST, pywa, Twilio, or anything else)
- Store sessions to a database — plug in your own `SessionStore`
- Parse raw WhatsApp webhook payloads — your webhook handler does that (it's a one-time ~40-line setup)
- Lock you into any web framework — FastAPI, Flask, Django, Lambda, raw asyncio — all fine

---

## 2. Core Concepts

```
Raw WA payload
      │
      ▼
  Your webhook  ──► builds IncomingMessage
      │
      ▼
  engine.process(incoming)
      │
      ▼
  List[Reply]   ──► your adapter sends each reply
```

**FlowTree** — a dictionary of named nodes you build once at startup.

**Node** — a single step in the conversation. Each node has a type (menu, input, action, etc.) and a `next` key pointing to the next node.

**Session** — per-user state the engine manages. Contains `current_node`, collected form data, navigation history, and arbitrary context your code can read/write.

**IncomingMessage** — a normalised message object you build from the raw WA payload and pass to the engine.

**Reply** — a structured response object the engine returns. You read `reply.node_type` and `reply.options` to decide how to send it (interactive list, buttons, plain text, document, etc.).

---

## 3. Quick Start

```python
from turnstack import BotEngine, FlowTree, IncomingMessage
from turnstack.nodes import Menu, Input, Action, Option, Field

# 1. Build the tree
tree = FlowTree(entry="welcome")

tree.add("welcome", Menu(
    text="👋 Welcome! What would you like to do?",
    options=[
        Option("📝 Book appointment", next="book_form"),
        Option("ℹ️ About us",         next="about"),
    ],
))

tree.add("book_form", Input(
    title="Booking",
    fields=[
        Field("name",  "What is your full name?"),
        Field("date",  "What date works for you? (YYYY-MM-DD)"),
    ],
    next="confirm_booking",
))

tree.add("confirm_booking", Action(
    fn=lambda session, collected: f"✅ Booking confirmed for {collected['name']} on {collected['date']}!",
    next="welcome",
))

tree.add("about", Action(
    fn=lambda s, c: "We are an example company. Reply anything to go back.",
    next="welcome",
))

# 2. Create the engine
engine = BotEngine(tree=tree)

# 3. In your webhook, normalise the payload and call process()
async def handle_message(user_id: str, text: str):
    incoming = IncomingMessage(user_id=user_id, type="text", text=text)
    replies  = await engine.process(incoming)
    for reply in replies:
        print(reply.body)   # send this via your WhatsApp provider
```

---

## 4. The Flow Tree

```python
from turnstack import FlowTree

tree = FlowTree(entry="welcome")
tree.add("welcome",  Menu(...))
tree.add("register", Input(...))
tree.add("done",     Action(...))
```

`FlowTree(entry="<node_key>")` — the `entry` key is where all new sessions start.

`tree.add(key, node)` — register a node. The key is a plain string; any node type is valid.

`tree.validate()` — called automatically when `BotEngine` starts. Raises if any `next` reference points to a missing node, or if no entry node is defined.

**Special destination key: `"__end__"`**

Use `next="__end__"` on any node to cleanly terminate the session. The engine sends the final message and the session is marked closed. The next message from the user starts a fresh session from the entry node.

```python
tree.add("goodbye", Action(
    fn=lambda s, c: "👋 Thanks for using our service. Goodbye!",
    next="__end__",
))
```

---

## 5. Building Blocks — Node Reference

### 5.1 Menu

Presents the user with a list of options. Renders as a WhatsApp interactive list message (with automatic pagination when options exceed the display limit).

```python
from turnstack.nodes import Menu, Option

tree.add("main_menu", Menu(
    text="What would you like to do?",
    options=[
        Option("🛒 Place order",    next="order_flow"),
        Option("📦 Track order",    next="track_flow"),
        Option("🆘 Support",        next="support_flow"),
        Option("❌ Cancel order",   next="cancel_flow"),
    ],
    button_label="Main Menu",     # label on the interactive list button
    header="MyCo Services",       # optional header
    footer="Reply 00 for home",   # optional footer
    allow_numeric=True,           # also accept "1", "2", "3"…
))
```

**`Option` fields:**

| Field | Type | Description |
|---|---|---|
| `label` | `str` | Displayed text (keep under 24 chars for buttons) |
| `next` | `str` | Node key to navigate to when selected |
| `value` | `str` | Value stored in collected / used as interactive ID. Defaults to `next`. |
| `description` | `str` | Optional subtitle in list-style menus (max 72 chars) |

When the user selects an option, the engine navigates to the `next` node. No code required.

---

### 5.2 Input

A multi-step form. Walks through a list of fields one at a time, validating each response before moving on. After all fields are collected, advances to `next`.

```python
from turnstack.nodes import Input, Field, MenuField, ButtonsField

tree.add("support_ticket", Input(
    title="Support Ticket",    # shown as "Support Ticket — Step 1 of 3"
    fields=[
        Field("summary",    "Briefly describe your issue:"),
        MenuField("priority", "How urgent is this?", options=[
            Option("🔴 Critical", value="critical"),
            Option("🟡 Medium",   value="medium"),
            Option("🟢 Low",      value="low"),
        ]),
        Field("contact_email", "What email should we reach you at?"),
    ],
    next="ticket_confirm",
))
```

| Argument | Type | Description |
|---|---|---|
| `fields` | `List[Field\|...]` | Ordered list of field objects (any mix of types) |
| `next` | `str` | Node to go to after all fields are collected |
| `title` | `str` | Optional flow title shown on each step |

The user can go `back` at any point to re-answer the previous field, or `0` to step back field by field within the same Input node.

---

### 5.3 Confirm

Presents a summary and asks the user to confirm before you commit a side effect.

```python
from turnstack.nodes import Confirm, Option

tree.add("ticket_confirm", Confirm(
    text=lambda collected: (
        f"Please confirm your ticket:\n\n"
        f"Issue: {collected['summary']}\n"
        f"Priority: {collected['priority']}\n"
        f"Email: {collected['contact_email']}"
    ),
    options=[
        Option("✅ Submit",   next="ticket_action"),
        Option("✏️ Edit",     next="support_ticket"),
        Option("❌ Cancel",   next="main_menu"),
    ],
))
```

`text` can be a plain string or a callable `(collected: dict) -> str`. The callable receives `session.collected` so you can summarise what the user entered.

The engine renders Confirm as interactive buttons (max 3 options, WhatsApp limit).

---

### 5.4 Action

Runs your Python function, sends the return value as a text message, then navigates to `next`.

```python
from turnstack.nodes import Action

tree.add("ticket_action", Action(
    fn=save_ticket,      # your function
    next="main_menu",
))

def save_ticket(session, collected):
    ticket_id = db.create_ticket(
        user_id  = session.user_id,
        summary  = collected["summary"],
        priority = collected["priority"],
        email    = collected["contact_email"],
    )
    return f"✅ Ticket #{ticket_id} created. We'll reply to {collected['contact_email']}."
```

**`fn` signature:** `(session: Session, collected: dict) -> str`

The string you return becomes the message body. If you return `None` or an empty string the engine sends no message body (useful when you only want a side effect before a menu appears).

`fn` can also be an `async` coroutine:

```python
async def async_action(session, collected):
    result = await external_api.call(collected["query"])
    return f"Result: {result}"
```

---

### 5.5 Router

Silently branches to a different node based on session state — no user input, no visible message. Use it as the entry point or at any junction where you need conditional routing.

```python
from turnstack.nodes import Router, Route

tree = FlowTree(entry="entry_router")

tree.add("entry_router", Router(
    before=load_user_profile,       # optional hook run before evaluation
    routes=[
        Route(when=lambda s: not s.context.get("user"),  next="onboarding"),
        Route(when=lambda s: s.context["user"]["role"] == "admin", next="admin_menu"),
    ],
    default="main_menu",            # fallback when no route matches
))

def load_user_profile(session):
    """before hook — populate session.context before route conditions run."""
    row = db.get_user(session.user_id)
    if row:
        session.context["user"] = dict(row)
```

`before` is called once before any `when` condition is evaluated. Use it to load data from your database into `session.context` so route conditions stay clean and declarative.

`Route.when` receives the full `session` object and must return `bool`. Routes are evaluated in order; the first `True` wins.

---

### 5.6 ListNode

Renders a dynamic list fetched at runtime with built-in pagination and optional interactive selection.

```python
from turnstack.nodes import ListNode, Option

tree.add("product_list", ListNode(
    fetch        = fetch_products,
    item_label   = lambda p: f"{p['name']} — Ksh {p['price']:,}",
    item_description = lambda p: p.get("category", ""),
    on_select    = "product_detail",
    title        = "🛒 Our Products",
    empty_text   = "No products available right now.",
    interactive  = True,
    button_label = "Browse",
    page_size    = 8,
    extra_options=[
        Option("🔙 Back to menu", next="main_menu"),
    ],
))

def fetch_products(session):
    """Simple fetch — returns a flat list."""
    return db.get_all_products()
```

**Paginated fetch** (when you have thousands of records):

```python
def fetch_products(session, page: int, page_size: int):
    """Paginated fetch — return (items_on_this_page, total_count)."""
    rows  = db.get_products(offset=page * page_size, limit=page_size)
    total = db.count_products()
    return rows, total
```

The engine detects which signature you use (3 params = paginated) and calls accordingly. Prev/Next navigation is added automatically.

When the user selects an item, the selected item's identifier is stored in `session.context["list_selected"]` and the engine navigates to `on_select`.

| Argument | Type | Default | Description |
|---|---|---|---|
| `fetch` | `Callable` | required | Simple or paginated fetch function |
| `item_label` | `Callable[[item], str]` | required | Display label for each item |
| `on_select` | `str` | required | Node to go to on selection |
| `title` | `str` | `"Select an option"` | Heading above the list |
| `empty_text` | `str` | `"No items available."` | Shown when fetch returns empty |
| `item_description` | `Callable[[item], str]` | `None` | Optional subtitle per item |
| `extra_options` | `List[Option]` | `[]` | Static options appended on last page (max 3) |
| `interactive` | `bool` | `False` | Render as interactive list |
| `button_label` | `str` | `"Options"` | Interactive list button label |
| `page_size` | `int` | `8` | Items per page (1–10) |

---

### 5.7 MediaReply

Generates a file (PDF, Excel, image, etc.) and sends it to the user, then automatically navigates to `next` and sends the next node's reply. Your adapter receives two `Reply` objects in the list — the file and the follow-up.

```python
from turnstack.nodes import MediaReply

tree.add("export_report", MediaReply(
    generate  = build_report,
    filename  = lambda s, c: f"report_{s.user_id}.xlsx",
    mime_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    caption   = "📊 Here is your report.",
    next      = "main_menu",
))

def build_report(session, collected) -> bytes:
    """Return raw file bytes."""
    wb = build_workbook(session.user_id)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
```

`generate` can be sync or `async`. `filename` and `caption` can be plain strings or callables `(session, collected) -> str`.

---

## 6. Field Types (inside Input)

### 6.1 Field / TextField

Plain text input. Accepts any text message.

```python
Field("full_name", "What is your full name?")
TextField("full_name", "What is your full name?")  # identical alias
```

With validation and transformation:

```python
Field(
    "age",
    "How old are you?",
    validate  = lambda v: "Must be a number." if not v.isdigit() else None,
    transform = int,
)
```

---

### 6.2 MenuField

Interactive list selection inside a form. The user picks one option; the value is stored in `session.collected`.

```python
MenuField(
    "department",
    "Which department?",
    options=[
        Option("Engineering",  value="eng"),
        Option("Sales",        value="sales"),
        Option("Operations",   value="ops"),
    ],
    button_label = "Choose Department",
    header       = "Departments",
    footer       = "Pick one",
    allow_numeric = True,
)
```

`options` can also be a callable `(session) -> List[Option]` for dynamic option lists built at runtime.

---

### 6.3 ButtonsField

Interactive reply buttons (max 3). Use when you have a small number of choices.

```python
ButtonsField(
    "approval",
    "Do you approve this request?",
    options=[
        Option("✅ Approve", value="approved"),
        Option("❌ Reject",  value="rejected"),
        Option("⏸ Hold",    value="on_hold"),
    ],
)
```

---

### 6.4 ImageField

Waits for the user to send a photo. Rejects anything else with a configurable message.

```python
ImageField(
    "profile_photo",
    "Please send a clear photo of yourself 📸",
    rejection_text="⚠️ That's not a photo. Please send an image.",
)
```

Collected value:

```python
{
    "media_id":  "wamid.xxx",    # WhatsApp media ID — use this to download
    "mime_type": "image/jpeg",
}
```

---

### 6.5 DocumentField

Waits for the user to send a document. Optionally restrict to specific MIME types.

```python
DocumentField(
    "id_document",
    "Upload a scanned copy of your national ID (PDF only) 📄",
    accept         = ["application/pdf"],
    rejection_text = "⚠️ Please upload a PDF file.",
)
```

Collected value:

```python
{
    "media_id":  "wamid.xxx",
    "mime_type": "application/pdf",
    "filename":  "id_scan.pdf",
}
```

---

### 6.6 LocationField

Sends a WhatsApp location-request message and waits for the user to share their location.

```python
LocationField(
    "pickup_location",
    "Please share your pickup location 📍",
    rejection_text = "⚠️ Please use the 📍 button to share your location.",
)
```

Collected value:

```python
{
    "latitude":  -1.286389,
    "longitude": 36.817223,
    "name":      "Nairobi CBD",    # may be None
    "address":   "Kenyatta Ave",   # may be None
}
```

---

### 6.7 BranchField

Conditionally injects a group of fields into the form based on earlier answers. The step counter updates dynamically — the user only sees steps relevant to their path.

```python
Input(
    title="Loan Application",
    fields=[
        ButtonsField("employment_type", "Are you employed or self-employed?", options=[
            Option("Employed",       value="employed"),
            Option("Self-employed",  value="self_employed"),
        ]),

        # Only shown for employed applicants
        BranchField(
            when=lambda s: s.collected.get("employment_type") == "employed",
            fields=[
                Field("employer_name", "Who is your employer?"),
                Field("monthly_salary", "What is your monthly salary (KES)?",
                      validate=lambda v: None if v.isdigit() else "Enter a number."),
            ],
        ),

        # Only shown for self-employed applicants
        BranchField(
            when=lambda s: s.collected.get("employment_type") == "self_employed",
            fields=[
                Field("business_name", "What is your business name?"),
                Field("monthly_revenue", "What is your average monthly revenue (KES)?"),
            ],
        ),

        Field("loan_amount", "How much would you like to borrow (KES)?"),
    ],
    next="loan_confirm",
)
```

`BranchField` is not itself a field — it has no `name`. It's a conditional wrapper that flattens transparently at runtime. Branches can be nested.

A field's `skip_if` argument is an alternative for single-field conditional skipping:

```python
Field(
    "company_name",
    "What is your company name?",
    skip_if=lambda s: s.collected.get("employment_type") == "self_employed",
)
```

---

## 7. The Engine

### 7.1 Instantiation

```python
from turnstack import BotEngine, FlowTree
from turnstack.stores.memory import InMemorySessionStore

engine = BotEngine(
    tree             = tree,
    session_store    = InMemorySessionStore(),  # default
    session_timeout  = 300,                     # seconds of inactivity before expiry
    back_keywords    = {"0", "back", "go back"},
    home_keywords    = {"00", "home", "menu", "start over"},
    exit_keywords    = {"000", "exit", "quit", "reset", "goodbye", "bye"},
    unsupported_text = "⚠️ Sorry, I can't process that message. Please try again.",
)
```

All parameters except `tree` are optional. The engine validates the tree on startup and raises immediately if any node reference is broken.

---

### 7.2 process()

```python
replies: List[Reply] = await engine.process(incoming)
```

The single public method you call for every inbound message. Always returns a `List[Reply]`.

In the common case the list contains one item. When a `MediaReply` node fires, the list contains two items — the file reply and the follow-up node — sent in order. You just loop:

```python
for reply in replies:
    await send_via_whatsapp(reply)
```

The engine handles everything internally:
- Session load / create / expire
- Global command interception (back, home, exit)
- Node dispatch and state transition
- Session save

You never touch the session store or call internal engine methods.

---

### 7.3 IncomingMessage

Build this from the raw WhatsApp webhook payload and pass it to `process()`.

```python
from turnstack import IncomingMessage

# Text message
IncomingMessage(
    user_id = "2547XXXXXXXX",
    type    = "text",
    text    = "Hello",
    raw     = raw_payload,      # optional, for your own reference
)

# Interactive selection (button or list reply)
IncomingMessage(
    user_id        = "2547XXXXXXXX",
    type           = "interactive",
    interactive_id = "option_value",   # the id from button_reply or list_reply
)

# Image
IncomingMessage(
    user_id    = "2547XXXXXXXX",
    type       = "image",
    media_id   = msg["image"]["id"],
    media_mime = msg["image"].get("mime_type"),
)

# Document
IncomingMessage(
    user_id    = "2547XXXXXXXX",
    type       = "document",
    media_id   = msg["document"]["id"],
    media_mime = msg["document"].get("mime_type"),
    media_name = msg["document"].get("filename"),
)

# Location
IncomingMessage(
    user_id  = "2547XXXXXXXX",
    type     = "location",
    location = {
        "latitude":  loc["latitude"],
        "longitude": loc["longitude"],
        "name":      loc.get("name"),
        "address":   loc.get("address"),
    },
)

# Unsupported type (sticker, audio, reaction…)
# Pass it through — engine replies politely and holds state
IncomingMessage(user_id="2547XXXXXXXX", type="sticker")
```

| Field | Type | Description |
|---|---|---|
| `user_id` | `str` | Unique user identifier (phone number or WA user ID) |
| `type` | `str` | `"text"`, `"interactive"`, `"image"`, `"document"`, `"location"`, or any other |
| `text` | `str\|None` | Text body (type=text) |
| `interactive_id` | `str\|None` | Selected option ID (type=interactive) |
| `media_id` | `str\|None` | WhatsApp media ID (type=image or document) |
| `media_mime` | `str\|None` | MIME type of the media |
| `media_name` | `str\|None` | Original filename (documents) |
| `location` | `dict\|None` | Location dict with latitude/longitude/name/address |
| `raw` | `Any` | Original raw payload — stored for your reference, engine ignores it |

---

### 7.4 Reply

The object returned by `process()`. Read its fields to decide how to send the message.

```python
@dataclass
class Reply:
    type:              Literal["text", "media", "end", "error"]
    body:              str           # message text / caption for media
    phone:             str           # recipient (same as user_id by default)

    # media
    file_bytes:        Optional[bytes]
    filename:          Optional[str]
    mime_type:         Optional[str]

    # interactive hints
    options:           List[ReplyOption]   # populated for menu/confirm nodes
    node_type:         Optional[str]       # "menu" | "confirm" | "input" | "input_buttons"
                                           # "input_location" | "list" | "media" | "text" | "error"
    suggested_replies: List[str]           # option labels for quick-reply chips

    # meta
    current_node:      Optional[str]
    session_state:     Optional[str]       # "new" | "active" | "expired"
    meta:              Dict[str, Any]      # extra hints — e.g. meta["button_label"]
```

**`ReplyOption`:**

```python
@dataclass
class ReplyOption:
    label:       str    # display text
    value:       str    # the id to send back when selected
    description: str    # optional subtitle (list menus)
```

**`node_type` reference — use this to decide message format:**

| `node_type` | What to send |
|---|---|
| `"menu"` | Interactive list message. Use `reply.options` and `reply.meta["button_label"]` |
| `"list"` | Interactive list (same as menu) |
| `"confirm"` | Interactive buttons (max 3). Use `reply.options` |
| `"input_buttons"` | Interactive buttons (ButtonsField inside Input) |
| `"input_location"` | Location request interactive message |
| `"input"` | Plain text prompt (TextField) |
| `"media"` | Document/image send. Use `file_bytes`, `filename`, `mime_type`, `body` as caption |
| `"text"` | Plain text message |
| `"error"` | Something went wrong — log and optionally show `body` to the user |

---

## 8. Session & State

### 8.1 Session object

The engine manages this for you. You only interact with it inside `fn`, `when`, `before`, `fetch`, `validate`, `transform`, and dynamic text callables.

```python
session.user_id        # str  — the user's identifier
session.current_node   # str  — which node the user is currently on
session.collected      # dict — all form values collected so far
session.context        # dict — your arbitrary data (not cleared between nodes)
session.nav_stack      # list — navigation history (for back/go home)
session.lifecycle_state  # "new" | "active" | "expired"
```

---

### 8.2 session.collected

Form data collected by `Input` nodes. Keys are the `name` values of your fields.

```python
def confirm_order(session, collected):
    return (
        f"Order summary:\n"
        f"Item:     {collected['item_name']}\n"
        f"Quantity: {collected['quantity']}\n"
        f"Address:  {collected['delivery_address']['address']}"
    )
```

`collected` is cleared when an `Input` node is entered fresh (not on back-navigation within it). Data from previous Input nodes persists until explicitly cleared or the session expires.

---

### 8.3 session.context

A free-form dict for your own data. Nothing in the engine reads or writes it (except `ListNode` which writes `context["list_selected"]` on item selection). Persists for the lifetime of the session.

```python
# In a Router before hook
def load_user(session):
    session.context["user"] = db.get_user(session.user_id)

# In a Menu text callable
Menu(
    text=lambda s, c=None: f"Hello {s.context['user']['first_name']}! What can I help you with?",
    ...
)

# In an Action
def process_order(session, collected):
    user = session.context["user"]
    ...
```

---

### 8.4 session.pagination

Stores page indices for menu and list pagination. Managed entirely by the engine — you should not write to this directly. Readable for debugging.

---

## 9. Session Stores

### 9.1 InMemorySessionStore

The default. Fast, zero-config, but sessions are lost on restart. Good for development.

```python
from turnstack.stores.memory import InMemorySessionStore

engine = BotEngine(tree=tree, session_store=InMemorySessionStore(session_timeout=600))
```

---

### 9.2 Custom Stores

Implement the `SessionStore` interface to persist sessions to Redis, a database, or anywhere:

```python
from turnstack.session import SessionStore, Session
import json

class RedisSessionStore(SessionStore):

    def __init__(self, redis_client, timeout: int = 300):
        self.redis   = redis_client
        self.timeout = timeout

    async def get(self, user_id: str) -> Session | None:
        data = await self.redis.get(f"session:{user_id}")
        if not data:
            return None
        return Session.from_dict(json.loads(data))

    async def save(self, session: Session) -> None:
        await self.redis.setex(
            f"session:{user_id}",
            self.timeout,
            json.dumps(session.to_dict()),
        )

    async def delete(self, user_id: str) -> None:
        await self.redis.delete(f"session:{user_id}")
```

Pass it to the engine:

```python
engine = BotEngine(tree=tree, session_store=RedisSessionStore(redis, timeout=300))
```

---

## 10. Navigation — Built-in Commands

The engine intercepts these plain-text messages before dispatching to any node handler. They work anywhere in the flow without any node configuration.

| Keyword(s) | Action |
|---|---|
| `0`, `back`, `go back` | Step back — goes to previous field inside an Input, or previous node |
| `00`, `home`, `menu`, `start over` | Jump to the entry node, clearing the navigation stack |
| `000`, `exit`, `quit`, `reset`, `goodbye`, `bye` | End the session — user receives a goodbye message; next message starts fresh |

All keyword sets are configurable on `BotEngine`:

```python
engine = BotEngine(
    tree           = tree,
    back_keywords  = {"b", "back"},
    home_keywords  = {"h", "home"},
    exit_keywords  = {"x", "exit"},
)
```

**Back within an Input node** is field-aware: pressing back steps to the previous field (clearing its collected value) rather than leaving the Input node entirely. Once at field 0, pressing back leaves the Input node and goes to the previous node in the stack.

---

## 11. Sending Replies — Adapter Pattern

TurnStack is send-agnostic. You read `reply.node_type` and `reply.options` to decide how to format the outgoing message, then send it however you like.

### 11.1 Reading reply fields

```python
replies = await engine.process(incoming)

for reply in replies:
    if reply.type == "error":
        logger.error(f"Engine error at {reply.current_node}: {reply.body}")
        continue

    await send(user_id=reply.phone, reply=reply)
```

### 11.2 Sending via REST

```python
async def send(user_id: str, phone: str, reply: Reply):

    if reply.type == "media":
        # Upload and send document/image
        media_id = await upload_media(reply.file_bytes, reply.mime_type, reply.filename)
        payload = {
            "messaging_product": "whatsapp",
            "to": phone,
            "type": "document",
            "document": {
                "id":      media_id,
                "caption": reply.body,
                "filename": reply.filename,
            },
        }

    elif reply.node_type in ("menu", "list"):
        # Interactive list
        rows = [
            {"id": opt.value, "title": opt.label[:24], "description": opt.description[:72]}
            for opt in reply.options
        ]
        payload = {
            "messaging_product": "whatsapp",
            "to": phone,
            "type": "interactive",
            "interactive": {
                "type": "list",
                "body": {"text": reply.body},
                "action": {
                    "button": reply.meta.get("button_label", "Options"),
                    "sections": [{"title": "Options", "rows": rows}],
                },
            },
        }

    elif reply.node_type in ("confirm", "input_buttons"):
        # Interactive buttons
        buttons = [
            {"type": "reply", "reply": {"id": opt.value, "title": opt.label[:20]}}
            for opt in reply.options[:3]
        ]
        payload = {
            "messaging_product": "whatsapp",
            "to": phone,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": reply.body},
                "action": {"buttons": buttons},
            },
        }

    elif reply.node_type == "input_location":
        payload = {
            "messaging_product": "whatsapp",
            "to": phone,
            "type": "interactive",
            "interactive": {
                "type": "location_request_message",
                "body": {"text": reply.body},
                "action": {"name": "send_location"},
            },
        }

    else:
        # Plain text (TextField prompt, Action message, error, etc.)
        payload = {
            "messaging_product": "whatsapp",
            "to": phone,
            "type": "text",
            "text": {"body": reply.body},
        }

    async with httpx.AsyncClient() as client:
        await client.post(
            f"https://graph.facebook.com/v19.0/{PHONE_ID}/messages",
            headers={"Authorization": f"Bearer {WA_TOKEN}"},
            json=payload,
        )
```

### 11.3 Sending via pywa / any library

If you use [pywa](https://github.com/david-lev/pywa) or another WhatsApp SDK, you adapt the same `reply.node_type` switch to your library's API:

```python
from pywa import WhatsApp
from pywa.types import Button, SectionList, Section, SectionRow

wa = WhatsApp(phone_id=PHONE_ID, token=WA_TOKEN)

async def send(reply: Reply):
    if reply.node_type in ("menu", "list"):
        rows = [SectionRow(id=o.value, title=o.label) for o in reply.options]
        await wa.send_message(
            to=reply.phone,
            text=reply.body,
            buttons=SectionList(
                button_title=reply.meta.get("button_label", "Options"),
                sections=[Section(title="Options", rows=rows)],
            ),
        )
    elif reply.node_type in ("confirm", "input_buttons"):
        btns = [Button(id=o.value, title=o.label) for o in reply.options]
        await wa.send_message(to=reply.phone, text=reply.body, buttons=btns)
    else:
        await wa.send_message(to=reply.phone, text=reply.body)
```

The engine's output is always the same structured `Reply` — the send layer is fully swappable.

---

## 12. Wiring to a Webhook

```python
from fastapi import FastAPI, Request, Response, HTTPException
import traceback

app = FastAPI()

@app.get("/webhook/whatsapp")
async def verify(request: Request):
    """WhatsApp webhook verification."""
    p = request.query_params
    if p.get("hub.mode") == "subscribe" and p.get("hub.verify_token") == WA_VERIFY_TOKEN:
        return Response(content=p.get("hub.challenge"), media_type="text/plain")
    raise HTTPException(403)


@app.post("/webhook/whatsapp")
async def webhook(request: Request):
    raw = await request.json()
    try:
        value = raw["entry"][0]["changes"][0]["value"]
        if "messages" not in value:
            return {"status": "no_messages"}

        msg      = value["messages"][0]
        phone    = msg.get("from", "")
        user_id  = msg.get("from_user_id", phone)   # fall back to phone if no user_id
        msg_type = msg.get("type", "")

        # ── normalise raw payload → IncomingMessage ───────────────────
        if msg_type == "text":
            incoming = IncomingMessage(
                user_id=user_id, type="text",
                text=msg["text"]["body"], raw=raw,
            )
        elif msg_type == "interactive":
            itype = msg["interactive"]["type"]
            iid   = (msg["interactive"]["button_reply"]["id"]
                     if itype == "button_reply"
                     else msg["interactive"]["list_reply"]["id"])
            incoming = IncomingMessage(
                user_id=user_id, type="interactive", interactive_id=iid, raw=raw,
            )
        elif msg_type == "image":
            incoming = IncomingMessage(
                user_id=user_id, type="image",
                media_id=msg["image"]["id"],
                media_mime=msg["image"].get("mime_type"), raw=raw,
            )
        elif msg_type == "document":
            incoming = IncomingMessage(
                user_id=user_id, type="document",
                media_id=msg["document"]["id"],
                media_mime=msg["document"].get("mime_type"),
                media_name=msg["document"].get("filename"), raw=raw,
            )
        elif msg_type == "location":
            loc = msg["location"]
            incoming = IncomingMessage(
                user_id=user_id, type="location",
                location={
                    "latitude":  loc.get("latitude"),
                    "longitude": loc.get("longitude"),
                    "name":      loc.get("name"),
                    "address":   loc.get("address"),
                }, raw=raw,
            )
        else:
            # Sticker, audio, reaction, etc. — engine handles gracefully
            incoming = IncomingMessage(user_id=user_id, type=msg_type, raw=raw)

        # ── process & send ────────────────────────────────────────────
        replies = await engine.process(incoming)
        for reply in replies:
            await send_whatsapp(user_id, phone, reply)

        return {"status": "ok"}

    except Exception:
        traceback.print_exc()
        raise HTTPException(500)
```

This webhook setup is a one-time boilerplate. After that your entire development effort is in the `FlowTree`.

---

## 13. Validation & Transformation

Every field type (`Field`, `MenuField`, `ButtonsField`, `ImageField`, `DocumentField`, `LocationField`) supports two optional hooks:

**`validate(value) -> str | None`**

Return an error message to reject the input. Return `None` to accept.

```python
import re

def validate_email(v: str):
    if not re.match(r"^[^@]+@[^@]+\.[^@]+$", v):
        return "⚠️ That doesn't look like a valid email address."
    return None

def validate_positive_integer(v: str):
    if not v.isdigit() or int(v) <= 0:
        return "⚠️ Please enter a positive whole number."
    return None

Field("email",    "Your email address?",        validate=validate_email)
Field("quantity", "How many units? (1–100)",    validate=validate_positive_integer)
```

When validation fails the engine re-asks the same question with the error message prepended. No state change occurs.

**`transform(value) -> Any`**

Applied after validation passes, before storing in `session.collected`. Use to cast types or normalise input.

```python
Field(
    "units",
    "How many units?",
    validate  = lambda v: None if v.isdigit() else "Please enter a number.",
    transform = int,      # stored as int, not string
)

Field(
    "full_name",
    "Your full name?",
    transform = str.strip,
)

Field(
    "date_of_birth",
    "Date of birth (YYYY-MM-DD)?",
    validate  = lambda v: None if re.match(r"\d{4}-\d{2}-\d{2}", v) else "Format: YYYY-MM-DD",
    transform = lambda v: datetime.strptime(v, "%Y-%m-%d").date(),
)
```

---

## 14. Dynamic Content

Most text-bearing arguments accept a callable so you can personalise the UI at runtime.

**Menu text:**

```python
Menu(
    text=lambda session: f"Hi {session.context.get('user', {}).get('name', 'there')}! What can I do for you?",
    options=[...],
)
```

Note: Menu `text` callable receives `(session)`. Confirm `text` callable receives `(collected)`.

**Option descriptions from a database:**

```python
MenuField(
    "branch",
    "Select your nearest branch:",
    options=lambda session: [
        Option(b["name"], value=str(b["id"]), description=b["address"])
        for b in db.get_branches(session.context.get("city"))
    ],
)
```

**Dynamic filename and caption on MediaReply:**

```python
MediaReply(
    generate  = build_statement,
    filename  = lambda s, c: f"statement_{s.context['user']['account_no']}.pdf",
    caption   = lambda s, c: f"📄 Statement for {c['period']}",
    mime_type = "application/pdf",
    next      = "main_menu",
)
```

---

## 15. Conditional Fields — BranchField

See [Section 6.7](#67-branchfield) for the full reference. Quick pattern summary:

```python
# Pattern: branch on a ButtonsField answer
Input(
    fields=[
        ButtonsField("type", "What are you reporting?", options=[
            Option("Bug",     value="bug"),
            Option("Feature", value="feature"),
        ]),
        BranchField(
            when=lambda s: s.collected.get("type") == "bug",
            fields=[
                Field("steps_to_reproduce", "How do you reproduce it?"),
                Field("expected_behaviour", "What did you expect to happen?"),
            ],
        ),
        BranchField(
            when=lambda s: s.collected.get("type") == "feature",
            fields=[
                Field("feature_description", "Describe the feature you'd like:"),
                Field("business_value",       "Why would this be valuable?"),
            ],
        ),
        Field("contact_email", "Your email for follow-up?"),
    ],
    next="submit_ticket",
)
```

The step counter shown to the user (`Step N of M`) reflects only the active fields for their path.

---

## 16. Pagination — Automatic Behaviour

**Menu pagination** kicks in automatically when a `Menu` or `MenuField` has more options than WhatsApp can show in a single interactive list. The engine:

1. Splits options into pages (max 8 real options per page, with Prev/Next controls)
2. Tracks the current page in `session.pagination`
3. Sends the correct page on each interaction

You do nothing. Just define as many options as you need.

**ListNode pagination** works the same way. For large datasets use the paginated fetch signature `(session, page, page_size) -> (items, total)` to avoid loading all records into memory.

**Page size** on `ListNode` is configurable (1–10, default 8):

```python
ListNode(fetch=..., ..., page_size=5)
```

---

## 17. Custom Node Handlers

If you need a node type that doesn't exist in TurnStack, register a custom handler:

```python
from turnstack.handlers.base import NodeHandler
from turnstack.reply import Reply
from turnstack.session import Session
from turnstack.message import IncomingMessage
from turnstack.tree import FlowTree

class PaymentPromptHandler(NodeHandler):
    async def handle(
        self,
        node: dict,
        session: Session,
        message: IncomingMessage,
        tree: FlowTree,
    ) -> Reply:
        # generate a payment link, store the reference, etc.
        ref = payment_gateway.create_link(session.user_id, node["amount"])
        session.context["payment_ref"] = ref

        self._transition_to(session, node.get("next", "main_menu"))
        return Reply(
            type="text",
            body=f"Please complete payment here: {ref['url']}",
            phone=session.user_id,
            node_type="text",
            current_node=session.current_node,
        )

# Register with the engine
engine.register_handler("payment_prompt", PaymentPromptHandler())

# Use in the tree
tree.add("pay_now", {
    "type":   "payment_prompt",
    "amount": 500,
    "next":   "payment_confirm",
})
```

---

## 18. Error Handling

The engine never raises exceptions to the caller. All internal errors produce a `Reply(type="error", ...)` with a descriptive `body`. In your adapter:

```python
for reply in replies:
    if reply.type == "error":
        logger.error(
            f"Engine error | node={reply.current_node} | {reply.body}"
        )
        # Optionally send a generic error message to the user
        await send_plain_text(reply.phone, "⚠️ Something went wrong. Please try again.")
        continue
    await send_whatsapp(reply.phone, reply)
```

**Common error causes:**

- A `next` key references a node that doesn't exist in the tree (caught at startup by `validate()`)
- A `generate` function in `MediaReply` raises an exception (logged in `body`)
- A `fetch` function in `ListNode` raises (logged in `body`)
- No handler registered for a node type (only happens with custom types)

**Your own exceptions in `Action.fn`** are caught and surfaced as error replies. It's good practice to catch expected exceptions yourself and return a user-friendly message:

```python
def save_order(session, collected):
    try:
        order_id = db.create_order(session.user_id, collected)
        return f"✅ Order #{order_id} placed!"
    except db.OutOfStockError:
        return "⚠️ Sorry, that item is out of stock. Please choose another."
    except Exception as e:
        logger.exception("Unexpected error saving order")
        return "⚠️ Something went wrong. Please try again later."
```

---

## 19. Debug Utilities

**Inspect all active sessions:**

```python
# InMemorySessionStore exposes .all()
for user_id, session in engine.session_store.all().items():
    print(user_id, session.current_node, session.collected)
```

**Reset a single session** (useful during development):

```python
await engine.session_store.delete("2547XXXXXXXX")
```

**Add a debug endpoint to your API:**

```python
@app.get("/debug/sessions")
async def debug_sessions():
    return {
        uid: {
            "node":      s.current_node,
            "state":     s.lifecycle_state,
            "collected": s.collected,
            "context":   s.context,
            "nav_stack": s.nav_stack,
        }
        for uid, s in engine.session_store.all().items()
    }

@app.delete("/debug/sessions/{user_id}")
async def reset_session(user_id: str):
    await engine.session_store.delete(user_id)
    return {"reset": user_id}
```

**Log reply metadata** in your send function:

```python
print(f"[{reply.session_state}] node={reply.current_node} type={reply.node_type} → {reply.body[:60]}")
```

---

## 20. Complete Example — Customer Support Bot

A complete, runnable example showing the majority of TurnStack features together.

```python
"""
support_bot.py
==============
Customer support bot using TurnStack.
"""

import asyncio
import traceback
import httpx
from fastapi import FastAPI, Request, Response, HTTPException
from turnstack import BotEngine, FlowTree, IncomingMessage
from turnstack.nodes import (
    Menu, Input, Confirm, Action, Router, ListNode, MediaReply,
    Option, Field, MenuField, ButtonsField, ImageField, DocumentField,
    LocationField, BranchField, Route,
)

# ── database (stub — replace with your real DB) ───────────────────────────────

users    = {}   # user_id -> {name, tier}
tickets  = []   # list of ticket dicts


def get_user(user_id):
    return users.get(user_id)

def save_user(user_id, name, tier):
    users[user_id] = {"name": name, "tier": tier}

def create_ticket(user_id, data):
    tid = len(tickets) + 1
    tickets.append({"id": tid, "user_id": user_id, **data})
    return tid

def get_tickets(user_id):
    return [t for t in tickets if t["user_id"] == user_id]


# ── router hooks ──────────────────────────────────────────────────────────────

def load_profile(session):
    user = get_user(session.user_id)
    if user:
        session.context["user"] = user


# ── action functions ──────────────────────────────────────────────────────────

def do_register(session, collected):
    save_user(session.user_id, collected["name"], collected["tier"])
    session.context["user"] = {"name": collected["name"], "tier": collected["tier"]}
    return f"✅ Welcome, {collected['name']}! Your account is set up."


def do_submit_ticket(session, collected):
    tid = create_ticket(session.user_id, {
        "type":    collected["ticket_type"],
        "summary": collected["summary"],
        "detail":  collected.get("detail"),
        "image_id": collected.get("screenshot", {}).get("media_id"),
    })
    return f"✅ Ticket #{tid} submitted. Our team will respond within 24 hours."


def do_learn_more(session, collected):
    tier = session.context.get("user", {}).get("tier", "standard")
    if tier == "premium":
        return "⭐ As a Premium member you get 24/7 live support and dedicated SLAs."
    return "📋 Standard support includes email responses within 24 hours."


# ── flow tree ─────────────────────────────────────────────────────────────────

tree = FlowTree(entry="entry")

# Entry router — send new users to onboarding, returning users to main menu
tree.add("entry", Router(
    before=load_profile,
    routes=[
        Route(when=lambda s: s.context.get("user") is None, next="welcome_new"),
    ],
    default="main_menu",
))

# New user welcome + registration
tree.add("welcome_new", Menu(
    text="👋 Welcome to SupportBot! Looks like you're new here. Let's get you set up.",
    options=[
        Option("Get started",  next="register"),
        Option("Learn more",   next="about_action"),
    ],
))

tree.add("about_action", Action(
    fn=lambda s, c: (
        "SupportBot lets you raise and track support tickets, "
        "download reports, and manage your account — all on WhatsApp."
    ),
    next="welcome_new",
))

tree.add("register", Input(
    title="Registration",
    fields=[
        Field("name", "What is your name?",
              validate=lambda v: "Name must be at least 2 characters." if len(v.strip()) < 2 else None,
              transform=str.strip),
        ButtonsField("tier", "Which plan are you on?", options=[
            Option("Standard", value="standard"),
            Option("Premium",  value="premium"),
        ]),
    ],
    next="register_action",
))

tree.add("register_action", Action(fn=do_register, next="main_menu"))

# Main menu
tree.add("main_menu", Menu(
    text=lambda s: f"Hi {s.context.get('user', {}).get('name', 'there')} 👋 How can I help?",
    options=[
        Option("🎫 New ticket",       next="new_ticket"),
        Option("📋 My tickets",       next="my_tickets"),
        Option("📊 Download report",  next="report_media"),
        Option("ℹ️  My plan",          next="plan_action"),
    ],
    button_label="Main Menu",
))

# New ticket flow (with conditional fields)
tree.add("new_ticket", Input(
    title="New Ticket",
    fields=[
        ButtonsField("ticket_type", "What type of issue is this?", options=[
            Option("🐛 Bug",     value="bug"),
            Option("💡 Feature", value="feature"),
            Option("❓ Question", value="question"),
        ]),
        Field("summary", "Describe your issue in one sentence:"),

        # Bug-only fields
        BranchField(
            when=lambda s: s.collected.get("ticket_type") == "bug",
            fields=[
                Field("detail", "What steps reproduce the bug?"),
                ImageField("screenshot", "Attach a screenshot (optional — send any text to skip):",
                           rejection_text="Please send an image or type 'skip'."),
            ],
        ),

        # Feature-only fields
        BranchField(
            when=lambda s: s.collected.get("ticket_type") == "feature",
            fields=[
                Field("detail", "Describe the feature you'd like in more detail:"),
            ],
        ),
    ],
    next="confirm_ticket",
))

tree.add("confirm_ticket", Confirm(
    text=lambda c: (
        f"📋 Ticket summary:\n\n"
        f"Type: {c['ticket_type']}\n"
        f"Issue: {c['summary']}\n"
        f"Details: {c.get('detail', '—')}\n\n"
        f"Submit this ticket?"
    ),
    options=[
        Option("✅ Submit",   next="submit_ticket_action"),
        Option("✏️ Edit",     next="new_ticket"),
        Option("❌ Cancel",   next="main_menu"),
    ],
))

tree.add("submit_ticket_action", Action(fn=do_submit_ticket, next="main_menu"))

# My tickets — dynamic list
tree.add("my_tickets", ListNode(
    fetch        = lambda session: get_tickets(session.user_id),
    item_label   = lambda t: f"#{t['id']} — {t['type']}",
    item_description = lambda t: t["summary"][:60],
    on_select    = "main_menu",   # in a real app: go to ticket detail node
    title        = "📋 Your Tickets",
    empty_text   = "You haven't raised any tickets yet.",
    interactive  = True,
    button_label = "My Tickets",
    extra_options=[Option("🔙 Back", next="main_menu")],
))

# Report download
tree.add("report_media", MediaReply(
    generate  = lambda session, collected: b"%PDF-1.4 ... (real PDF bytes here)",
    filename  = lambda s, c: f"report_{s.user_id}.pdf",
    mime_type = "application/pdf",
    caption   = "📊 Here is your support report.",
    next      = "main_menu",
))

# Plan info
tree.add("plan_action", Action(fn=do_learn_more, next="main_menu"))

# ── engine ────────────────────────────────────────────────────────────────────

engine = BotEngine(tree=tree, session_timeout=600)

# ── WhatsApp send helper (REST) ───────────────────────────────────────────────

import os
WA_TOKEN    = os.getenv("WA_TOKEN", "")
WA_PHONE_ID = os.getenv("WA_PHONE_ID", "")

async def send_whatsapp(user_id: str, phone: str, reply):
    from turnstack.reply import Reply
    url     = f"https://graph.facebook.com/v19.0/{WA_PHONE_ID}/messages"
    headers = {"Authorization": f"Bearer {WA_TOKEN}", "Content-Type": "application/json"}

    if reply.type == "media":
        # upload first, then send — simplified here
        payload = {"messaging_product": "whatsapp", "to": phone, "type": "text",
                   "text": {"body": f"[File: {reply.filename}] {reply.body}"}}

    elif reply.node_type in ("menu", "list"):
        rows = [{"id": o.value, "title": o.label[:24]} for o in reply.options]
        payload = {
            "messaging_product": "whatsapp", "to": phone, "type": "interactive",
            "interactive": {
                "type": "list", "body": {"text": reply.body},
                "action": {
                    "button": reply.meta.get("button_label", "Options"),
                    "sections": [{"title": "Options", "rows": rows}],
                },
            },
        }

    elif reply.node_type in ("confirm", "input_buttons"):
        buttons = [{"type": "reply", "reply": {"id": o.value, "title": o.label[:20]}}
                   for o in reply.options[:3]]
        payload = {
            "messaging_product": "whatsapp", "to": phone, "type": "interactive",
            "interactive": {
                "type": "button", "body": {"text": reply.body},
                "action": {"buttons": buttons},
            },
        }

    elif reply.node_type == "input_location":
        payload = {
            "messaging_product": "whatsapp", "to": phone, "type": "interactive",
            "interactive": {
                "type": "location_request_message",
                "body": {"text": reply.body},
                "action": {"name": "send_location"},
            },
        }

    else:
        payload = {"messaging_product": "whatsapp", "to": phone,
                   "type": "text", "text": {"body": reply.body}}

    async with httpx.AsyncClient() as client:
        await client.post(url, json=payload, headers=headers)


# ── FastAPI webhook ───────────────────────────────────────────────────────────

app = FastAPI()

@app.get("/webhook/whatsapp")
async def verify(request: Request):
    p = request.query_params
    if p.get("hub.mode") == "subscribe" and p.get("hub.verify_token") == os.getenv("WA_VERIFY_TOKEN"):
        return Response(content=p.get("hub.challenge"), media_type="text/plain")
    raise HTTPException(403)

@app.post("/webhook/whatsapp")
async def webhook(request: Request):
    raw = await request.json()
    try:
        value = raw["entry"][0]["changes"][0]["value"]
        if "messages" not in value:
            return {"status": "no_messages"}

        msg      = value["messages"][0]
        phone    = msg.get("from", "")
        user_id  = msg.get("from_user_id", phone)
        msg_type = msg.get("type", "")

        if msg_type == "text":
            incoming = IncomingMessage(user_id=user_id, type="text",
                                       text=msg["text"]["body"], raw=raw)
        elif msg_type == "interactive":
            itype = msg["interactive"]["type"]
            iid   = (msg["interactive"]["button_reply"]["id"]
                     if itype == "button_reply"
                     else msg["interactive"]["list_reply"]["id"])
            incoming = IncomingMessage(user_id=user_id, type="interactive",
                                       interactive_id=iid, raw=raw)
        elif msg_type == "image":
            incoming = IncomingMessage(user_id=user_id, type="image",
                                       media_id=msg["image"]["id"],
                                       media_mime=msg["image"].get("mime_type"), raw=raw)
        elif msg_type == "document":
            incoming = IncomingMessage(user_id=user_id, type="document",
                                       media_id=msg["document"]["id"],
                                       media_mime=msg["document"].get("mime_type"),
                                       media_name=msg["document"].get("filename"), raw=raw)
        elif msg_type == "location":
            loc = msg["location"]
            incoming = IncomingMessage(user_id=user_id, type="location",
                                       location={"latitude": loc.get("latitude"),
                                                 "longitude": loc.get("longitude"),
                                                 "name": loc.get("name"),
                                                 "address": loc.get("address")}, raw=raw)
        else:
            incoming = IncomingMessage(user_id=user_id, type=msg_type, raw=raw)

        replies = await engine.process(incoming)
        for reply in replies:
            await send_whatsapp(user_id, phone, reply)

        return {"status": "ok"}

    except Exception:
        traceback.print_exc()
        raise HTTPException(500)
```

---

*TurnStack — build the conversation, not the plumbing.*
