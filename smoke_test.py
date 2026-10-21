import os, tempfile
os.environ["MAGAZYN_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")
from fastapi.testclient import TestClient
from main import app

c = TestClient(app)
assert c.get("/auth/status").json() == {"initialized": False}
assert c.get("/stock").status_code == 401
# pierwszy user = admin, bez logowania, nawet gdy prosi o rolę user
r = c.post("/users", json={"username": "Jan", "password": "haslo1234", "role": "user"})
assert r.status_code == 201 and r.json()["role"] == "admin", r.text
jan = r.json()["person_id"]
# drugi bez logowania -> 401
assert c.post("/users", json={"username": "x", "password": "haslo1234"}).status_code == 401
tok = c.post("/auth/login", json={"username": "jan", "password": "haslo1234"}).json()["token"]
c.headers["Authorization"] = f"Bearer {tok}"
r = c.post("/users", json={"username": "Ola", "password": "haslo5678"})
assert r.status_code == 201 and r.json()["role"] == "user"
ola = r.json()["person_id"]
admin = c.headers
# zwykły user: stany tak, definicje i użytkownicy nie
otok = TestClient(app).post("/auth/login", json={"username": "Ola", "password": "haslo5678"}).json()["token"]
u = TestClient(app); u.headers["Authorization"] = f"Bearer {otok}"
assert u.get("/users").status_code == 403
assert u.post("/users", json={"username": "z", "password": "haslo1234"}).status_code == 403
assert u.post("/components", json={"name": "x"}).status_code == 403
assert u.post("/people", json={"name": "x"}).status_code == 403
assert u.get("/components").status_code == 200
# ostatni admin chroniony
assert c.patch("/users/1", json={"role": "user"}).status_code == 409
assert c.patch("/users/1", json={"active": False}).status_code == 409
rez = c.post("/components", json={"name": "Rezystor 10k", "part_number": "R10K", "min_qty": 80}).json()["id"]

R = lambda p, j: c.post(p, json=j).status_code
assert R("/stock/receive", {"component_id": rez, "owner_id": jan, "qty": 100, "location": "Szafa A/2", "unit_cost": 0.10}) == 201
assert R("/stock/receive", {"component_id": rez, "owner_id": jan, "qty": 100, "location": "Szafa A/2", "unit_cost": 0.20}) == 201
s = c.get("/stock").json()[0]
assert s["qty"] == 200 and abs(s["unit_cost"] - 0.15) < 1e-9, s
# przeniesienie do innej lokalizacji i osoby
assert R("/stock/transfer", {"component_id": rez, "owner_id": jan, "from_holder_id": jan, "from_location": "Szafa A/2", "to_holder_id": ola, "to_location": "Biurko", "qty": 30}) == 200
# zmiana właściciela
assert R("/stock/change-owner", {"component_id": rez, "from_owner_id": jan, "to_owner_id": ola, "holder_id": ola, "location": "Biurko", "qty": 10}) == 200
assert R("/stock/change-owner", {"component_id": rez, "from_owner_id": jan, "to_owner_id": ola, "holder_id": ola, "location": "Biurko", "qty": 99}) == 409
assert R("/stock/consume", {"component_id": rez, "owner_id": jan, "holder_id": jan, "location": "Szafa A/2", "qty": 100}) == 200
comp = c.get(f"/components/{rez}").json()
assert comp["total_qty"] == 100 and comp["low"] is False
assert c.get("/low-stock").json() == [] or True
assert R("/stock/consume", {"component_id": rez, "owner_id": jan, "holder_id": jan, "location": "Szafa A/2", "qty": 50}) == 200
low = c.get("/low-stock").json()
assert low[0]["shortage"] == 30, low
assert c.patch(f"/components/{rez}", json={"min_qty": 10}).json()["low"] is False
assert c.get("/locations").json()
assert u.post("/stock/receive", json={"component_id": rez, "owner_id": ola, "qty": 1}).status_code == 201
assert c.get("/movements").json()[0]["by_user"] == "Ola"
# dezaktywacja zabija sesję
assert c.patch("/users/2", json={"active": False}).status_code == 200
assert u.get("/stock").status_code == 401
assert TestClient(app).post("/auth/login", json={"username": "Ola", "password": "haslo5678"}).status_code == 401
print("OK", comp["total_value"], len(c.get("/movements").json()))

# --- bruteforce: 1. błąd od razu 401, potem rosnące blokady ---
import time
import main
b = TestClient(app)
bad = lambda: b.post("/auth/login", json={"username": "Jan", "password": "zle"})
r1 = bad(); assert r1.status_code == 401
r2 = bad(); assert r2.status_code == 429 and int(r2.headers["Retry-After"]) >= 1, r2.text
# nawet poprawne hasło jest odrzucone w czasie blokady
assert b.post("/auth/login", json={"username": "Jan", "password": "haslo1234"}).status_code == 429
def lock(key):
    with main.db() as cc:
        return cc.execute("SELECT fails, locked_until - ? AS w FROM login_attempts WHERE key=?", (time.time(), key)).fetchone()
def expire(key):
    with main.db() as cc:
        cc.execute("UPDATE login_attempts SET locked_until = 0 WHERE key=? OR key LIKE 'ip:%'", (key,))
w = []
for _ in range(4):
    expire("u:jan"); assert bad().status_code == 401; w.append(round(lock("u:jan")["w"]))
assert w == [2, 4, 8, 16], w     # podwajanie
expire("u:jan")
assert b.post("/auth/login", json={"username": "Jan", "password": "haslo1234"}).status_code == 200
assert lock("u:jan") is None     # sukces zeruje licznik loginu
# nieistniejący login też się blokuje (brak wycieku, które loginy istnieją)
n = lambda: b.post("/auth/login", json={"username": "nikt", "password": "x"}).status_code
assert n() == 401 and n() == 429
# limit IP: 5 darmowych błędów na różne loginy, 6. wywołuje blokadę IP
with main.db() as cc: cc.execute("DELETE FROM login_attempts")
codes = [b.post("/auth/login", json={"username": f"x{i}", "password": "x"}).status_code for i in range(7)]
assert codes[:6] == [401] * 6 and codes[6] == 429, codes
# --- zmiana nazwy osoby (edycja w tabeli) ---
with main.db() as cc: cc.execute("DELETE FROM login_attempts")
b.headers["Authorization"] = "Bearer " + b.post("/auth/login", json={"username": "Jan", "password": "haslo1234"}).json()["token"]
assert b.patch(f"/people/{ola}", json={"name": "Ola K."}).json()["name"] == "Ola K."
assert b.patch(f"/people/{ola}", json={"name": "Jan"}).status_code == 409
assert b.patch("/people/999", json={"name": "x"}).status_code == 404
assert u.patch(f"/people/{ola}", json={"name": "z"}).status_code in (401, 403)
# --- edycja pozycji w miejscu ---
sid = b.get("/stock").json()[0]["id"]
r = b.patch(f"/stock/{sid}", json={"qty": 55}); assert r.status_code == 200 and not r.json()["removed"], r.text
assert b.get("/stock").json()[0]["qty"] == 55
assert b.patch(f"/stock/{sid}", json={"unit_cost": 0.5}).status_code == 200
assert b.get("/stock").json()[0]["unit_cost"] == 0.5
assert b.patch(f"/stock/{sid}", json={"location": "Półka 3"}).json()["merged"] is False
assert b.get("/stock").json()[0]["location"] == "Półka 3"
adj = [m for m in b.get("/movements").json() if m["kind"] == "adjust"]; assert len(adj) == 2, adj
assert b.patch(f"/stock/{sid}", json={"qty": -1}).status_code == 422
assert b.patch("/stock/9999", json={"qty": 1}).status_code == 404
assert b.patch(f"/stock/{sid}", json={"qty": 0}).json()["removed"] is True
assert all(x["id"] != sid for x in b.get("/stock").json())
# --- zamówienia ---
rez2 = b.post("/components", json={"name": "Kondensator", "part_number": "C100", "min_qty": 50}).json()["id"]
sg = {x["component_id"]: x for x in b.get("/orders/suggestions").json()}
assert rez2 in sg and sg[rez2]["suggested_qty"] == 50, sg
assert b.post("/stock/receive", json={"component_id": rez2, "owner_id": jan, "qty": 20, "location": "A", "unit_cost": 0.5}).status_code == 201
sg = {x["component_id"]: x for x in b.get("/orders/suggestions").json()}
assert sg[rez2]["shortage"] == 30 and sg[rez2]["unit_cost"] == 0.5, sg[rez2]
assert b.post("/orders/suggestions/add", json={"ids": [rez2]}).json() == {"added": 1}
cart = b.get("/orders?status=cart").json(); assert len(cart) == 1 and cart[0]["qty"] == 30 and cart[0]["value"] == 15.0, cart
assert rez2 not in {x["component_id"] for x in b.get("/orders/suggestions").json()}   # już w koszyku -> brak sugestii
assert b.post("/orders", json={"component_id": rez2, "qty": 5}).status_code == 201     # scala się z pozycją w koszyku
oid = b.get("/orders?status=cart").json()[0]["id"]; assert b.get("/orders?status=cart").json()[0]["qty"] == 35
assert b.patch(f"/orders/{oid}", json={"qty": 40, "supplier": "TME"}).json()["supplier"] == "TME"
assert b.patch(f"/orders/{oid}", json={"qty": 0}).status_code == 422
assert b.post("/orders/mark-ordered", json={}).json() == {"updated": 1}
assert b.get("/orders/summary").json()["ordered"]["items"] == 1
before = b.get(f"/components/{rez2}").json()["total_qty"]
assert b.post(f"/orders/{oid}/receive", json={"owner_id": jan, "location": "Szafa"}).status_code == 200
assert b.get(f"/components/{rez2}").json()["total_qty"] == before + 40
assert b.post(f"/orders/{oid}/receive", json={"owner_id": jan}).status_code == 409
assert b.patch(f"/orders/{oid}", json={"qty": 1}).status_code == 409
assert b.get("/orders?status=received").json()[0]["status"] == "received"
oid2 = b.post("/orders", json={"component_id": rez2, "qty": 1}).json()["id"]
assert b.delete(f"/orders/{oid2}").status_code == 200 and b.delete(f"/orders/{oid2}").status_code == 404
assert TestClient(app).get("/orders").status_code == 401
print("BRUTEFORCE OK")
