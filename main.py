"""Magazyn: kto jest właścicielem komponentu, kto go ma i ile sztuk."""
import hashlib
import hmac
import os
import math
import secrets
import sqlite3
import time
from contextlib import closing, contextmanager

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

DB_PATH = os.environ.get("MAGAZYN_DB", os.path.join(os.path.dirname(__file__), "magazyn.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS people (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin','user')),
    active INTEGER NOT NULL DEFAULT 1,
    person_id INTEGER NOT NULL REFERENCES people(id)   -- osoba, na którą wpisują się stany tego użytkownika
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at TEXT NOT NULL
);
-- zamówienia: koszyk rzeczy do kupienia -> zamówione -> przyjęte na stan
CREATE TABLE IF NOT EXISTS order_items (
    id INTEGER PRIMARY KEY,
    component_id INTEGER NOT NULL REFERENCES components(id),
    qty INTEGER NOT NULL CHECK (qty > 0),
    unit_cost REAL,
    supplier TEXT,
    note TEXT,
    status TEXT NOT NULL DEFAULT 'cart' CHECK (status IN ('cart','ordered','received')),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    created_by INTEGER,
    ordered_at TEXT,
    received_at TEXT
);
CREATE TABLE IF NOT EXISTS login_attempts (
    key TEXT PRIMARY KEY,            -- "u:<login>" albo "ip:<adres>"
    fails INTEGER NOT NULL,
    last_fail REAL NOT NULL,
    locked_until REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS components (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    part_number TEXT UNIQUE,
    description TEXT,
    min_qty INTEGER NOT NULL DEFAULT 0   -- próg: poniżej tej sumy komponent "się kończy"
);
-- stan: komponent X, właściciel owner, fizycznie u holder, w miejscu location
CREATE TABLE IF NOT EXISTS stock (
    id INTEGER PRIMARY KEY,
    component_id INTEGER NOT NULL REFERENCES components(id),
    owner_id INTEGER NOT NULL REFERENCES people(id),
    holder_id INTEGER NOT NULL REFERENCES people(id),
    location TEXT NOT NULL DEFAULT '',
    qty INTEGER NOT NULL CHECK (qty >= 0),
    unit_cost REAL,                      -- średnia ważona cena zakupu za sztukę
    UNIQUE (component_id, owner_id, holder_id, location)
);
CREATE TABLE IF NOT EXISTS movements (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    kind TEXT NOT NULL,              -- receive | transfer | change_owner | consume
    component_id INTEGER NOT NULL,
    owner_id INTEGER NOT NULL,
    to_owner_id INTEGER,
    from_holder_id INTEGER,
    to_holder_id INTEGER,
    from_location TEXT,
    to_location TEXT,
    qty INTEGER NOT NULL,
    unit_cost REAL,
    note TEXT,
    user_id INTEGER                  -- kto wykonał operację
);
"""


@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


with closing(sqlite3.connect(DB_PATH)) as _c:
    _c.executescript(SCHEMA)
    if "user_id" not in [r[1] for r in _c.execute("PRAGMA table_info(movements)")]:
        _c.execute("ALTER TABLE movements ADD COLUMN user_id INTEGER")
        _c.commit()

app = FastAPI(title="Magazyn", description="Stany magazynowe: właściciel / posiadacz / ilość")


# ---------- uwierzytelnianie ----------
SESSION_DAYS = 30
MIN_PASSWORD = 8
bearer = HTTPBearer(auto_error=False)


def _hash_password(pw: str) -> str:
    salt = secrets.token_bytes(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"{salt.hex()}${h.hex()}"


def _verify_password(pw: str, stored: str) -> bool:
    salt, h = stored.split("$")
    cand = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1)
    return hmac.compare_digest(cand, bytes.fromhex(h))


_DUMMY_HASH = _hash_password("dummy-password")  # wyrównuje czas logowania nieistniejącego użytkownika


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _new_session(conn, user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    conn.execute("DELETE FROM sessions WHERE expires_at <= strftime('%Y-%m-%dT%H:%M:%SZ','now')")
    conn.execute(
        "INSERT INTO sessions (token_hash, user_id, expires_at) "
        "VALUES (?,?,strftime('%Y-%m-%dT%H:%M:%SZ','now',?))",
        (_token_hash(token), user_id, f"+{SESSION_DAYS} days"),
    )
    return token


def _user_from_token(creds: HTTPAuthorizationCredentials | None):
    if not creds:
        return None
    with db() as c:
        row = c.execute(
            """SELECT u.id, u.username, u.role, u.person_id FROM sessions s JOIN users u ON u.id = s.user_id
               WHERE s.token_hash = ? AND u.active = 1
                 AND s.expires_at > strftime('%Y-%m-%dT%H:%M:%SZ','now')""",
            (_token_hash(creds.credentials),),
        ).fetchone()
    return dict(row) if row else None


def current_user(creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> dict:
    user = _user_from_token(creds)
    if not user:
        raise HTTPException(401, "Wymagane zalogowanie", headers={"WWW-Authenticate": "Bearer"})
    return user


def require_admin(user: dict = Depends(current_user)) -> dict:
    if user["role"] != "admin":
        raise HTTPException(403, "Wymagane uprawnienia administratora")
    return user


AUTH = [Depends(current_user)]
ADMIN = [Depends(require_admin)]


# ---------- ochrona przed bruteforce ----------
# Po k-tej nieudanej próbie (ponad limit "free") kolejna próba jest blokowana na min(2^(k-free-1), MAX) s:
# 1. błąd -> od razu odpowiedź, bez blokady (user free=0 => blokada 1 s już po pierwszym błędzie),
# kolejne blokady 1 s, 2 s, 4 s, 8 s ... do MAX. Licznik zerowany po udanym logowaniu (dla loginu)
# albo gdy od ostatniego błędu minęło RESET_AFTER.
# Blokada nie usypia wątku: serwer zwraca 429 + Retry-After.
LOCK_MAX = 15 * 60
RESET_AFTER = 60 * 60
FREE_USER = 0   # błędów bez blokady na login
FREE_IP = 5     # błędów bez blokady na adres IP (wiele osób może siedzieć za jednym NAT-em)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _attempt_keys(request: Request, username: str):
    return [(f"u:{username.strip().lower()}", FREE_USER), (f"ip:{_client_ip(request)}", FREE_IP)]


def _check_not_locked(conn, keys):
    now = time.time()
    wait = 0.0
    for key, _ in keys:
        row = conn.execute("SELECT locked_until FROM login_attempts WHERE key = ?", (key,)).fetchone()
        if row:
            wait = max(wait, row["locked_until"] - now)
    if wait > 0:
        secs = math.ceil(wait)
        raise HTTPException(429, f"Zbyt wiele prób logowania. Spróbuj za {secs} s",
                            headers={"Retry-After": str(secs)})


def _register_failure(conn, keys):
    now = time.time()
    conn.execute("DELETE FROM login_attempts WHERE last_fail < ? AND locked_until < ?", (now - RESET_AFTER, now))
    for key, free in keys:
        row = conn.execute("SELECT fails, last_fail FROM login_attempts WHERE key = ?", (key,)).fetchone()
        fails = (row["fails"] if row and now - row["last_fail"] < RESET_AFTER else 0) + 1
        lock = min(2 ** (fails - free - 1), LOCK_MAX) if fails > free else 0
        conn.execute(
            """INSERT INTO login_attempts (key, fails, last_fail, locked_until) VALUES (?,?,?,?)
               ON CONFLICT(key) DO UPDATE SET fails = excluded.fails, last_fail = excluded.last_fail,
                                              locked_until = excluded.locked_until""",
            (key, fails, now, now + lock),
        )


def _register_success(conn, keys):
    conn.execute("DELETE FROM login_attempts WHERE key = ?", (keys[0][0],))  # tylko licznik loginu


class LoginIn(BaseModel):
    username: str
    password: str


class UserIn(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=MIN_PASSWORD)
    role: str = Field("user", pattern="^(admin|user)$")
    person_name: str | None = Field(None, description="nazwa osoby w magazynie; domyślnie = username")


class UserPatch(BaseModel):
    password: str | None = Field(None, min_length=MIN_PASSWORD)
    role: str | None = Field(None, pattern="^(admin|user)$")
    active: bool | None = None


class PasswordIn(BaseModel):
    old_password: str
    new_password: str = Field(min_length=MIN_PASSWORD)


USER_SELECT = """SELECT u.id, u.username, u.role, u.active, u.person_id, p.name AS person
                 FROM users u JOIN people p ON p.id = u.person_id"""


def _user_out(conn, user_id: int) -> dict:
    return dict(conn.execute(USER_SELECT + " WHERE u.id = ?", (user_id,)).fetchone())


def _other_active_admins(conn, user_id: int) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM users WHERE role='admin' AND active=1 AND id != ?", (user_id,)
    ).fetchone()[0]


@app.get("/auth/status")
def auth_status():
    """initialized=false -> pierwszy POST /users tworzy administratora."""
    with db() as c:
        return {"initialized": c.execute("SELECT COUNT(*) FROM users").fetchone()[0] > 0}


@app.post("/auth/login")
def login(l: LoginIn, request: Request):
    keys = _attempt_keys(request, l.username)
    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        _check_not_locked(c, keys)  # przed scryptem: zablokowany nie obciąża CPU
        u = c.execute("SELECT id, password_hash, active FROM users WHERE username = ?", (l.username,)).fetchone()
        ok = _verify_password(l.password, u["password_hash"] if u else _DUMMY_HASH)
        if u and ok and u["active"]:
            _register_success(c, keys)
            return {"token": _new_session(c, u["id"]), "user": _user_out(c, u["id"])}
        _register_failure(c, keys)
    # wyjątek poza blokiem db(), żeby zapis licznika nie został wycofany
    raise HTTPException(401, "Zły login lub hasło")


@app.post("/auth/logout")
def logout(creds: HTTPAuthorizationCredentials = Depends(bearer), _: dict = Depends(current_user)):
    with db() as c:
        c.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(creds.credentials),))
    return {"ok": True}


@app.get("/auth/me")
def me(u: dict = Depends(current_user)):
    return u


@app.post("/auth/password")
def change_password(p: PasswordIn, request: Request, u: dict = Depends(current_user)):
    keys = _attempt_keys(request, u["username"])
    with db() as c:
        c.execute("BEGIN IMMEDIATE")
        _check_not_locked(c, keys)
        row = c.execute("SELECT password_hash FROM users WHERE id = ?", (u["id"],)).fetchone()
        if _verify_password(p.old_password, row["password_hash"]):
            _register_success(c, keys)
            c.execute("UPDATE users SET password_hash = ? WHERE id = ?", (_hash_password(p.new_password), u["id"]))
            c.execute("DELETE FROM sessions WHERE user_id = ?", (u["id"],))
            return {"token": _new_session(c, u["id"])}
        _register_failure(c, keys)
    raise HTTPException(403, "Stare hasło jest nieprawidłowe")


# ---------- zarządzanie użytkownikami ----------
@app.post("/users", status_code=201)
def create_user(x: UserIn, creds: HTTPAuthorizationCredentials | None = Depends(bearer)):
    """Gdy nie ma jeszcze żadnego użytkownika: bez logowania, a konto zawsze zostaje administratorem.
    Potem tylko administrator."""
    pw_hash = _hash_password(x.password)
    with db() as c:
        c.execute("BEGIN IMMEDIATE")  # blokada zapisu: dwa równoległe "pierwsze" konta nie przejdą
        first = c.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
        if first:
            role = "admin"
        else:
            caller = _user_from_token(creds)
            if not caller:
                raise HTTPException(401, "Wymagane zalogowanie", headers={"WWW-Authenticate": "Bearer"})
            if caller["role"] != "admin":
                raise HTTPException(403, "Wymagane uprawnienia administratora")
            role = x.role
        pname = x.person_name or x.username
        person = c.execute("SELECT id FROM people WHERE name = ?", (pname,)).fetchone()
        person_id = person["id"] if person else c.execute("INSERT INTO people (name) VALUES (?)", (pname,)).lastrowid
        try:
            uid = c.execute(
                "INSERT INTO users (username, password_hash, role, person_id) VALUES (?,?,?,?)",
                (x.username, pw_hash, role, person_id),
            ).lastrowid
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Taki użytkownik już istnieje")
        return _user_out(c, uid)


@app.get("/users", dependencies=ADMIN)
def list_users():
    with db() as c:
        return [dict(r) for r in c.execute(USER_SELECT + " ORDER BY u.username")]


@app.patch("/users/{user_id}", dependencies=ADMIN)
def patch_user(user_id: int, p: UserPatch):
    """Zmiana hasła / roli / aktywności. Użytkowników nie usuwamy (zostają w historii), tylko dezaktywujemy."""
    with db() as c:
        target = c.execute("SELECT role, active FROM users WHERE id = ?", (user_id,)).fetchone()
        if not target:
            raise HTTPException(404, "Nie ma takiego użytkownika")
        new_role = p.role or target["role"]
        new_active = target["active"] if p.active is None else int(p.active)
        loses_admin = target["role"] == "admin" and target["active"] and (new_role != "admin" or not new_active)
        if loses_admin and _other_active_admins(c, user_id) == 0:
            raise HTTPException(409, "Nie można odebrać uprawnień ostatniemu aktywnemu administratorowi")
        c.execute("UPDATE users SET role = ?, active = ? WHERE id = ?", (new_role, new_active, user_id))
        if p.password:
            c.execute("UPDATE users SET password_hash = ? WHERE id = ?", (_hash_password(p.password), user_id))
        if p.password or not new_active:
            c.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        return _user_out(c, user_id)


# ---------- modele ----------
class PersonIn(BaseModel):
    name: str = Field(min_length=1)


class ComponentIn(BaseModel):
    name: str = Field(min_length=1)
    part_number: str | None = None
    description: str | None = None
    min_qty: int = Field(0, ge=0, description="próg alarmowy (suma sztuk)")


class ComponentPatch(BaseModel):
    name: str | None = Field(None, min_length=1)
    part_number: str | None = None
    description: str | None = None
    min_qty: int | None = Field(None, ge=0)


class ReceiveIn(BaseModel):
    """Przyjęcie sztuk na stan (zakup)."""
    component_id: int
    owner_id: int
    holder_id: int | None = None  # domyślnie = owner
    location: str = ""
    qty: int = Field(gt=0)
    unit_cost: float | None = Field(None, ge=0, description="cena zakupu za sztukę")
    note: str | None = None


class TransferIn(BaseModel):
    """Przekazanie sztuk innej osobie i/lub w inne miejsce (właściciel bez zmian)."""
    component_id: int
    owner_id: int
    from_holder_id: int
    from_location: str = ""
    to_holder_id: int | None = None   # domyślnie ten sam posiadacz
    to_location: str | None = None    # domyślnie to samo miejsce
    qty: int = Field(gt=0)
    note: str | None = None


class StockPatch(BaseModel):
    """Edycja pozycji "w miejscu": korekta ilości / ceny albo zmiana lokalizacji."""
    qty: int | None = Field(None, ge=0, description="0 = usuń pozycję")
    unit_cost: float | None = Field(None, ge=0)
    location: str | None = None


class ChangeOwnerIn(BaseModel):
    """Zmiana właściciela (sztuki zostają u posiadacza i w miejscu)."""
    component_id: int
    from_owner_id: int
    to_owner_id: int
    holder_id: int
    location: str = ""
    qty: int = Field(gt=0)
    note: str | None = None


class ConsumeIn(BaseModel):
    """Zużycie / zdjęcie ze stanu."""
    component_id: int
    owner_id: int
    holder_id: int
    location: str = ""
    qty: int = Field(gt=0)
    note: str | None = None


# ---------- pomocnicze ----------
def _exists(conn, table: str, id_: int, label: str):
    if not conn.execute(f"SELECT 1 FROM {table} WHERE id = ?", (id_,)).fetchone():
        raise HTTPException(404, f"Nie ma: {label} id={id_}")


def _add_stock(conn, comp, owner, holder, loc, qty, unit_cost):
    row = conn.execute(
        "SELECT qty, unit_cost FROM stock WHERE component_id=? AND owner_id=? AND holder_id=? AND location=?",
        (comp, owner, holder, loc),
    ).fetchone()
    if not row:
        conn.execute(
            "INSERT INTO stock (component_id, owner_id, holder_id, location, qty, unit_cost) VALUES (?,?,?,?,?,?)",
            (comp, owner, holder, loc, qty, unit_cost),
        )
        return
    old_cost = row["unit_cost"]
    if unit_cost is None:
        cost = old_cost
    elif old_cost is None:
        cost = unit_cost
    else:  # średnia ważona
        cost = (row["qty"] * old_cost + qty * unit_cost) / (row["qty"] + qty)
    conn.execute(
        "UPDATE stock SET qty = qty + ?, unit_cost = ? WHERE component_id=? AND owner_id=? AND holder_id=? AND location=?",
        (qty, cost, comp, owner, holder, loc),
    )


def _remove_stock(conn, comp, owner, holder, loc, qty):
    """Zdejmuje sztuki, zwraca unit_cost wiersza."""
    row = conn.execute(
        "SELECT qty, unit_cost FROM stock WHERE component_id=? AND owner_id=? AND holder_id=? AND location=?",
        (comp, owner, holder, loc),
    ).fetchone()
    have = row["qty"] if row else 0
    if have < qty:
        raise HTTPException(409, f"Za mało sztuk: jest {have}, żądano {qty}")
    conn.execute(
        "UPDATE stock SET qty = qty - ? WHERE component_id=? AND owner_id=? AND holder_id=? AND location=?",
        (qty, comp, owner, holder, loc),
    )
    conn.execute("DELETE FROM stock WHERE qty = 0")
    return row["unit_cost"]


def _log(conn, kind, comp, owner, qty, *, to_owner=None, frm=None, to=None,
         from_loc=None, to_loc=None, cost=None, note=None, user=None):
    conn.execute(
        """INSERT INTO movements (kind, component_id, owner_id, to_owner_id, from_holder_id, to_holder_id,
                                  from_location, to_location, qty, unit_cost, note, user_id)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (kind, comp, owner, to_owner, frm, to, from_loc, to_loc, qty, cost, note, user),
    )


# ---------- osoby ----------
@app.post("/people", dependencies=ADMIN, status_code=201)
def create_person(p: PersonIn):
    with db() as c:
        try:
            cur = c.execute("INSERT INTO people (name) VALUES (?)", (p.name,))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Osoba o takiej nazwie już istnieje")
        return {"id": cur.lastrowid, "name": p.name}


@app.patch("/people/{person_id}", dependencies=ADMIN)
def rename_person(person_id: int, p: PersonIn):
    with db() as c:
        _exists(c, "people", person_id, "osoba")
        try:
            c.execute("UPDATE people SET name = ? WHERE id = ?", (p.name, person_id))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Osoba o takiej nazwie już istnieje")
        return {"id": person_id, "name": p.name}


@app.get("/people", dependencies=AUTH)
def list_people():
    with db() as c:
        return [dict(r) for r in c.execute("SELECT * FROM people ORDER BY name")]


# ---------- komponenty ----------
COMPONENT_SELECT = """SELECT c.*, COALESCE(SUM(s.qty),0) AS total_qty,
                             COALESCE(SUM(s.qty * s.unit_cost),0) AS total_value
                      FROM components c LEFT JOIN stock s ON s.component_id = c.id"""


@app.post("/components", dependencies=ADMIN, status_code=201)
def create_component(comp: ComponentIn):
    with db() as c:
        try:
            cur = c.execute(
                "INSERT INTO components (name, part_number, description, min_qty) VALUES (?,?,?,?)",
                (comp.name, comp.part_number, comp.description, comp.min_qty),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Komponent o tym part_number już istnieje")
        return {"id": cur.lastrowid, **comp.model_dump()}


@app.patch("/components/{component_id}", dependencies=ADMIN)
def patch_component(component_id: int, p: ComponentPatch):
    fields = p.model_dump(exclude_unset=True)
    with db() as c:
        _exists(c, "components", component_id, "komponent")
        if fields:
            sets = ", ".join(f"{k} = ?" for k in fields)
            try:
                c.execute(f"UPDATE components SET {sets} WHERE id = ?", [*fields.values(), component_id])
            except sqlite3.IntegrityError:
                raise HTTPException(409, "Komponent o tym part_number już istnieje")
    return get_component(component_id)


@app.get("/components", dependencies=AUTH)
def list_components(q: str | None = None):
    sql, args = COMPONENT_SELECT, []
    if q:
        sql += " WHERE c.name LIKE ? OR c.part_number LIKE ?"
        args += [f"%{q}%"] * 2
    sql += " GROUP BY c.id ORDER BY c.name"
    with db() as c:
        return [dict(r) for r in c.execute(sql, args)]


@app.get("/low-stock", dependencies=AUTH)
def low_stock():
    """Komponenty, których suma sztuk jest poniżej progu min_qty (najpilniejsze pierwsze)."""
    sql = (COMPONENT_SELECT + """ WHERE c.min_qty > 0 GROUP BY c.id
              HAVING total_qty < c.min_qty ORDER BY (c.min_qty - total_qty) DESC""")
    with db() as c:
        return [{**dict(r), "shortage": r["min_qty"] - r["total_qty"]} for r in c.execute(sql)]


@app.get("/components/{component_id}", dependencies=AUTH)
def get_component(component_id: int):
    with db() as c:
        comp = c.execute(COMPONENT_SELECT + " WHERE c.id = ? GROUP BY c.id", (component_id,)).fetchone()
        if not comp:
            raise HTTPException(404, "Nie ma takiego komponentu")
        rows = c.execute(
            """SELECT o.name AS owner, h.name AS holder, s.owner_id, s.holder_id, s.location,
                      s.qty, s.unit_cost, s.qty * s.unit_cost AS value
               FROM stock s JOIN people o ON o.id = s.owner_id JOIN people h ON h.id = s.holder_id
               WHERE s.component_id = ? ORDER BY o.name, h.name, s.location""",
            (component_id,),
        ).fetchall()
        return {**dict(comp), "low": 0 < comp["min_qty"] > comp["total_qty"], "stock": [dict(r) for r in rows]}


# ---------- stany ----------
@app.get("/stock", dependencies=AUTH)
def list_stock(
    component_id: int | None = None,
    owner_id: int | None = Query(None, description="filtruj po właścicielu"),
    holder_id: int | None = Query(None, description="filtruj po posiadaczu"),
    location: str | None = Query(None, description="fragment nazwy lokalizacji"),
):
    sql = """SELECT s.id, c.id AS component_id, c.name AS component, c.part_number,
                    o.id AS owner_id, o.name AS owner, h.id AS holder_id, h.name AS holder,
                    s.location, s.qty, s.unit_cost, s.qty * s.unit_cost AS value
             FROM stock s JOIN components c ON c.id = s.component_id
             JOIN people o ON o.id = s.owner_id JOIN people h ON h.id = s.holder_id WHERE 1=1"""
    args: list = []
    for col, val in (("c.id", component_id), ("o.id", owner_id), ("h.id", holder_id)):
        if val is not None:
            sql += f" AND {col} = ?"
            args.append(val)
    if location:
        sql += " AND s.location LIKE ?"
        args.append(f"%{location}%")
    sql += " ORDER BY c.name, o.name, h.name, s.location"
    with db() as c:
        return [dict(r) for r in c.execute(sql, args)]


@app.get("/locations", dependencies=AUTH)
def list_locations():
    """Używane lokalizacje z liczbą pozycji, sztuk i wartością."""
    with db() as c:
        return [dict(r) for r in c.execute(
            """SELECT location, COUNT(*) AS items, SUM(qty) AS total_qty, SUM(qty * unit_cost) AS total_value
               FROM stock GROUP BY location ORDER BY location""")]


@app.patch("/stock/{stock_id}", dependencies=AUTH)
def patch_stock(stock_id: int, p: StockPatch, u: dict = Depends(current_user)):
    fields = p.model_fields_set
    merged = removed = False
    with db() as c:
        row = c.execute("SELECT * FROM stock WHERE id = ?", (stock_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Nie ma takiej pozycji")
        comp, owner, holder = row["component_id"], row["owner_id"], row["holder_id"]
        loc = row["location"]

        # 1) zmiana lokalizacji: cała pozycja jedzie w nowe miejsce (scala się z istniejącą w tym miejscu)
        if "location" in fields and p.location is not None and p.location.strip() != loc:
            new_loc = p.location.strip()
            existing = c.execute(
                "SELECT id FROM stock WHERE component_id=? AND owner_id=? AND holder_id=? AND location=?",
                (comp, owner, holder, new_loc),
            ).fetchone()
            if existing:
                c.execute("DELETE FROM stock WHERE id = ?", (stock_id,))
                _add_stock(c, comp, owner, holder, new_loc, row["qty"], row["unit_cost"])
                stock_id, merged = existing["id"], True
            else:
                c.execute("UPDATE stock SET location = ? WHERE id = ?", (new_loc, stock_id))
            _log(c, "transfer", comp, owner, row["qty"], frm=holder, to=holder, from_loc=loc, to_loc=new_loc,
                 cost=row["unit_cost"], note="Zmiana lokalizacji", user=u["id"])
            row = c.execute("SELECT * FROM stock WHERE id = ?", (stock_id,)).fetchone()
            loc = new_loc

        # 2) korekta ilości (0 usuwa pozycję)
        if "qty" in fields and p.qty is not None and p.qty != row["qty"]:
            _log(c, "adjust", comp, owner, abs(p.qty - row["qty"]), frm=holder, to=holder, from_loc=loc, to_loc=loc,
                 cost=row["unit_cost"], note=f"Korekta ilości: {row['qty']} → {p.qty}", user=u["id"])
            if p.qty == 0:
                c.execute("DELETE FROM stock WHERE id = ?", (stock_id,))
                removed = True
            else:
                c.execute("UPDATE stock SET qty = ? WHERE id = ?", (p.qty, stock_id))
            row = c.execute("SELECT * FROM stock WHERE id = ?", (stock_id,)).fetchone()

        # 3) korekta ceny jednostkowej
        if not removed and "unit_cost" in fields and p.unit_cost != row["unit_cost"]:
            _log(c, "adjust", comp, owner, row["qty"], frm=holder, to=holder, from_loc=loc, to_loc=loc,
                 cost=p.unit_cost, note=f"Korekta ceny: {row['unit_cost']} → {p.unit_cost}", user=u["id"])
            c.execute("UPDATE stock SET unit_cost = ? WHERE id = ?", (p.unit_cost, stock_id))
    return {"id": stock_id, "merged": merged, "removed": removed}


@app.post("/stock/receive", dependencies=AUTH, status_code=201)
def receive(r: ReceiveIn, u: dict = Depends(current_user)):
    holder = r.holder_id or r.owner_id
    with db() as c:
        _exists(c, "components", r.component_id, "komponent")
        _exists(c, "people", r.owner_id, "właściciel")
        _exists(c, "people", holder, "posiadacz")
        _add_stock(c, r.component_id, r.owner_id, holder, r.location, r.qty, r.unit_cost)
        _log(c, "receive", r.component_id, r.owner_id, r.qty, to=holder, to_loc=r.location,
             cost=r.unit_cost, note=r.note, user=u["id"])
    return {"ok": True}


@app.post("/stock/transfer", dependencies=AUTH)
def transfer(t: TransferIn, u: dict = Depends(current_user)):
    to_holder = t.to_holder_id or t.from_holder_id
    to_loc = t.from_location if t.to_location is None else t.to_location
    if to_holder == t.from_holder_id and to_loc == t.from_location:
        raise HTTPException(400, "Źródło i cel są takie same")
    with db() as c:
        _exists(c, "components", t.component_id, "komponent")
        _exists(c, "people", t.owner_id, "właściciel")
        _exists(c, "people", to_holder, "odbiorca")
        cost = _remove_stock(c, t.component_id, t.owner_id, t.from_holder_id, t.from_location, t.qty)
        _add_stock(c, t.component_id, t.owner_id, to_holder, to_loc, t.qty, cost)
        _log(c, "transfer", t.component_id, t.owner_id, t.qty, frm=t.from_holder_id, to=to_holder,
             from_loc=t.from_location, to_loc=to_loc, cost=cost, note=t.note, user=u["id"])
    return {"ok": True}


@app.post("/stock/change-owner", dependencies=AUTH)
def change_owner(x: ChangeOwnerIn, u: dict = Depends(current_user)):
    if x.from_owner_id == x.to_owner_id:
        raise HTTPException(400, "Właściciel jest ten sam")
    with db() as c:
        _exists(c, "components", x.component_id, "komponent")
        _exists(c, "people", x.to_owner_id, "nowy właściciel")
        cost = _remove_stock(c, x.component_id, x.from_owner_id, x.holder_id, x.location, x.qty)
        _add_stock(c, x.component_id, x.to_owner_id, x.holder_id, x.location, x.qty, cost)
        _log(c, "change_owner", x.component_id, x.from_owner_id, x.qty, to_owner=x.to_owner_id,
             frm=x.holder_id, to=x.holder_id, from_loc=x.location, to_loc=x.location, cost=cost, note=x.note, user=u["id"])
    return {"ok": True}


@app.post("/stock/consume", dependencies=AUTH)
def consume(x: ConsumeIn, u: dict = Depends(current_user)):
    with db() as c:
        _exists(c, "components", x.component_id, "komponent")
        cost = _remove_stock(c, x.component_id, x.owner_id, x.holder_id, x.location, x.qty)
        _log(c, "consume", x.component_id, x.owner_id, x.qty, frm=x.holder_id, from_loc=x.location,
             cost=cost, note=x.note, user=u["id"])
    return {"ok": True}


@app.get("/movements", dependencies=AUTH)
def movements(component_id: int | None = None, limit: int = Query(100, le=1000)):
    sql = """SELECT m.id, m.ts, m.kind, c.name AS component, o.name AS owner, no.name AS new_owner,
                    f.name AS from_holder, t.name AS to_holder, m.from_location, m.to_location,
                    m.qty, m.unit_cost, m.qty * m.unit_cost AS value, m.note, us.username AS by_user
             FROM movements m JOIN components c ON c.id = m.component_id
             JOIN people o ON o.id = m.owner_id
             LEFT JOIN people no ON no.id = m.to_owner_id
             LEFT JOIN people f ON f.id = m.from_holder_id
             LEFT JOIN people t ON t.id = m.to_holder_id
             LEFT JOIN users us ON us.id = m.user_id"""
    args: list = []
    if component_id is not None:
        sql += " WHERE m.component_id = ?"
        args.append(component_id)
    sql += " ORDER BY m.id DESC LIMIT ?"
    args.append(limit)
    with db() as c:
        return [dict(r) for r in c.execute(sql, args)]


# ---------- zamówienia (koszyk zakupów) ----------
NOW_SQL = "strftime('%Y-%m-%dT%H:%M:%SZ','now')"
ORDER_SELECT = """SELECT o.id, o.component_id, c.name AS component, c.part_number, o.qty, o.unit_cost,
                         o.qty * o.unit_cost AS value, o.supplier, o.note, o.status,
                         o.created_at, o.ordered_at, o.received_at, u.username AS by_user,
                         (SELECT COALESCE(SUM(qty),0) FROM stock WHERE component_id = o.component_id) AS in_stock,
                         c.min_qty
                  FROM order_items o JOIN components c ON c.id = o.component_id
                  LEFT JOIN users u ON u.id = o.created_by"""


class OrderIn(BaseModel):
    component_id: int
    qty: int = Field(gt=0)
    unit_cost: float | None = Field(None, ge=0)
    supplier: str | None = None
    note: str | None = None


class OrderPatch(BaseModel):
    qty: int | None = Field(None, gt=0)
    unit_cost: float | None = Field(None, ge=0)
    supplier: str | None = None
    note: str | None = None
    status: str | None = Field(None, pattern="^(cart|ordered)$")


class OrderReceiveIn(BaseModel):
    """Przyjęcie zamówionych sztuk na stan."""
    owner_id: int
    holder_id: int | None = None
    location: str = ""
    qty: int | None = Field(None, gt=0, description="faktycznie dostarczona ilość (domyślnie jak w zamówieniu)")
    unit_cost: float | None = Field(None, ge=0)


class IdsIn(BaseModel):
    ids: list[int] | None = None  # None = wszystkie


def _suggested_cost(conn, component_id: int):
    """Średnia ważona cena ze stanu, a gdy brak: cena z ostatniego przyjęcia."""
    row = conn.execute(
        "SELECT SUM(qty * unit_cost) / SUM(qty) AS c FROM stock WHERE component_id = ? AND unit_cost IS NOT NULL",
        (component_id,),
    ).fetchone()
    if row and row["c"] is not None:
        return round(row["c"], 4)
    row = conn.execute(
        "SELECT unit_cost FROM movements WHERE component_id = ? AND kind = 'receive' AND unit_cost IS NOT NULL "
        "ORDER BY id DESC LIMIT 1", (component_id,),
    ).fetchone()
    return row["unit_cost"] if row else None


def _suggestions(conn):
    """Sugestie z listy "kończy się": brakująca ilość pomniejszona o to, co już jest w koszyku / zamówione."""
    out = []
    rows = conn.execute(
        """SELECT c.id, c.name, c.part_number, c.min_qty, COALESCE(SUM(s.qty),0) AS in_stock
           FROM components c LEFT JOIN stock s ON s.component_id = c.id
           WHERE c.min_qty > 0 GROUP BY c.id HAVING in_stock < c.min_qty"""
    ).fetchall()
    for r in rows:
        on_order = conn.execute(
            "SELECT COALESCE(SUM(qty),0) FROM order_items WHERE component_id = ? AND status IN ('cart','ordered')",
            (r["id"],),
        ).fetchone()[0]
        shortage = r["min_qty"] - r["in_stock"]
        needed = shortage - on_order
        if needed <= 0:
            continue
        cost = _suggested_cost(conn, r["id"])
        out.append({
            "component_id": r["id"], "component": r["name"], "part_number": r["part_number"],
            "in_stock": r["in_stock"], "min_qty": r["min_qty"], "shortage": shortage, "on_order": on_order,
            "suggested_qty": needed, "unit_cost": cost, "value": needed * cost if cost is not None else None,
        })
    out.sort(key=lambda x: -x["shortage"])
    return out


def _add_to_cart(conn, user_id, component_id, qty, unit_cost=None, supplier=None, note=None):
    """Dodaje do koszyka; ta sama pozycja w koszyku scala się (sumuje ilość)."""
    existing = conn.execute(
        "SELECT id FROM order_items WHERE component_id = ? AND status = 'cart' ORDER BY id LIMIT 1", (component_id,)
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE order_items SET qty = qty + ?, unit_cost = COALESCE(?, unit_cost), "
            "supplier = COALESCE(?, supplier), note = COALESCE(?, note) WHERE id = ?",
            (qty, unit_cost, supplier, note, existing["id"]),
        )
        return existing["id"]
    return conn.execute(
        "INSERT INTO order_items (component_id, qty, unit_cost, supplier, note, created_by) VALUES (?,?,?,?,?,?)",
        (component_id, qty, unit_cost, supplier, note, user_id),
    ).lastrowid


@app.get("/orders", dependencies=AUTH)
def list_orders(status: str | None = Query(None, pattern="^(cart|ordered|received)$"), limit: int = Query(500, le=2000)):
    sql, args = ORDER_SELECT, []
    if status:
        sql += " WHERE o.status = ?"
        args.append(status)
    sql += " ORDER BY o.id DESC LIMIT ?"
    args.append(limit)
    with db() as c:
        return [dict(r) for r in c.execute(sql, args)]


@app.get("/orders/summary", dependencies=AUTH)
def orders_summary():
    with db() as c:
        def agg(st):
            r = c.execute("SELECT COUNT(*) n, COALESCE(SUM(qty),0) q, COALESCE(SUM(qty*unit_cost),0) v "
                          "FROM order_items WHERE status = ?", (st,)).fetchone()
            return {"items": r["n"], "qty": r["q"], "value": r["v"]}
        return {"cart": agg("cart"), "ordered": agg("ordered"), "suggestions": len(_suggestions(c))}


@app.get("/orders/suggestions", dependencies=AUTH)
def order_suggestions():
    with db() as c:
        return _suggestions(c)


@app.post("/orders/suggestions/add")
def add_suggestions(x: IdsIn, u: dict = Depends(current_user)):
    """Wrzuca sugestie (wszystkie albo wskazane komponenty) do koszyka w proponowanej ilości."""
    with db() as c:
        added = 0
        for sgg in _suggestions(c):
            if x.ids is not None and sgg["component_id"] not in x.ids:
                continue
            _add_to_cart(c, u["id"], sgg["component_id"], sgg["suggested_qty"], sgg["unit_cost"], note="sugestia: kończy się")
            added += 1
    return {"added": added}


@app.post("/orders/mark-ordered")
def mark_ordered(x: IdsIn):
    """Oznacza pozycje koszyka jako zamówione (wszystkie albo wskazane)."""
    with db() as c:
        if x.ids is None:
            cur = c.execute(f"UPDATE order_items SET status='ordered', ordered_at={NOW_SQL} WHERE status='cart'")
        else:
            marks = ",".join("?" * len(x.ids)) or "NULL"
            cur = c.execute(f"UPDATE order_items SET status='ordered', ordered_at={NOW_SQL} "
                            f"WHERE status='cart' AND id IN ({marks})", x.ids)
    return {"updated": cur.rowcount}


@app.post("/orders", status_code=201)
def create_order(o: OrderIn, u: dict = Depends(current_user)):
    with db() as c:
        _exists(c, "components", o.component_id, "komponent")
        oid = _add_to_cart(c, u["id"], o.component_id, o.qty, o.unit_cost, o.supplier, o.note)
        return dict(c.execute(ORDER_SELECT + " WHERE o.id = ?", (oid,)).fetchone())


@app.patch("/orders/{order_id}")
def patch_order(order_id: int, p: OrderPatch, u: dict = Depends(current_user)):
    fields = p.model_fields_set
    with db() as c:
        row = c.execute("SELECT * FROM order_items WHERE id = ?", (order_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Nie ma takiej pozycji zamówienia")
        if row["status"] == "received":
            raise HTTPException(409, "Pozycja jest już przyjęta na stan")
        for f in ("qty", "unit_cost", "supplier", "note"):
            if f in fields:
                v = getattr(p, f)
                if f in ("supplier", "note") and isinstance(v, str):
                    v = v.strip() or None
                if f == "qty" and v is None:
                    continue
                c.execute(f"UPDATE order_items SET {f} = ? WHERE id = ?", (v, order_id))
        if p.status and p.status != row["status"]:
            if p.status == "ordered":
                c.execute(f"UPDATE order_items SET status='ordered', ordered_at={NOW_SQL} WHERE id = ?", (order_id,))
            else:
                c.execute("UPDATE order_items SET status='cart', ordered_at=NULL WHERE id = ?", (order_id,))
        return dict(c.execute(ORDER_SELECT + " WHERE o.id = ?", (order_id,)).fetchone())


@app.delete("/orders/{order_id}")
def delete_order(order_id: int):
    with db() as c:
        if not c.execute("DELETE FROM order_items WHERE id = ?", (order_id,)).rowcount:
            raise HTTPException(404, "Nie ma takiej pozycji zamówienia")
    return {"ok": True}


@app.post("/orders/{order_id}/receive")
def receive_order(order_id: int, r: OrderReceiveIn, u: dict = Depends(current_user)):
    """Dostawa doszła: pozycja trafia na stan (jak /stock/receive) i znika z zamówień."""
    with db() as c:
        row = c.execute("SELECT * FROM order_items WHERE id = ?", (order_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Nie ma takiej pozycji zamówienia")
        if row["status"] == "received":
            raise HTTPException(409, "Pozycja jest już przyjęta na stan")
        holder = r.holder_id or r.owner_id
        _exists(c, "people", r.owner_id, "właściciel")
        _exists(c, "people", holder, "posiadacz")
        qty = r.qty or row["qty"]
        cost = r.unit_cost if r.unit_cost is not None else row["unit_cost"]
        loc = r.location.strip()
        _add_stock(c, row["component_id"], r.owner_id, holder, loc, qty, cost)
        _log(c, "receive", row["component_id"], r.owner_id, qty, to=holder, to_loc=loc, cost=cost,
             note=f"Zamówienie #{order_id}", user=u["id"])
        c.execute(f"UPDATE order_items SET status='received', qty=?, unit_cost=?, received_at={NOW_SQL} WHERE id=?",
                  (qty, cost, order_id))
    return {"ok": True}


# ---------- frontend (SPA) ----------
MEDIA_EXT = (".mp4", ".avif", ".jpg", ".jpeg", ".png", ".webp", ".woff2")   # już skompresowane, bez gzip


class SelectiveGZip(GZipMiddleware):
    """gzip dla tekstu (JSON, HTML, JS, CSS); pliki medialne są pomijane (gzip psułby też zakresy Range dla wideo)."""

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"].lower().endswith(MEDIA_EXT):
            return await self.app(scope, receive, send)
        await super().__call__(scope, receive, send)


class CachedStatic(StaticFiles):
    """Kod (html/js/css) zawsze rewalidowany przez ETag (szybkie 304), media cache'owane na dobę."""

    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        if resp.status_code in (200, 206, 304):
            resp.headers["Cache-Control"] = "public, max-age=86400" if path.lower().endswith(MEDIA_EXT) else "no-cache"
        return resp


app.add_middleware(SelectiveGZip, minimum_size=1024)

# montowane na końcu, żeby nie przykrywać tras API
app.mount("/", CachedStatic(directory=os.path.join(os.path.dirname(__file__), "static"), html=True), name="ui")

# opisy endpointów dla /docs, /redoc i API.md (patrz apidocs.py)
from apidocs import document  # noqa: E402

document(app)
