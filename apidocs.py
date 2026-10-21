"""Dokumentacja API: opisy endpointów dla /docs i /redoc oraz generator API.md.

Opisy trzymane są tutaj, a nie w main.py, żeby nie zaśmiecać logiki. `document(app)` nakłada je na
zarejestrowane trasy (tagi, podsumowania, opisy, kody błędów, przykłady). `python apidocs.py` generuje
API.md z aktualnej specyfikacji OpenAPI, więc dokument nie rozjeżdża się z kodem.
"""
from __future__ import annotations

import json
import sys

from fastapi.routing import APIRoute

DESCRIPTION = """
Stany magazynowe: **kto jest właścicielem komponentu, kto go ma i w jakiej ilości**, z lokalizacją,
kosztami zakupu, listą rzeczy które się kończą i koszykiem zamówień.

## Szybki start
1. `GET /auth/status`: gdy `initialized=false`, pierwszy `POST /users` (bez logowania) tworzy **administratora**.
2. `POST /auth/login` zwraca `token`. Wysyłaj go w nagłówku `Authorization: Bearer <token>`
   (w tej dokumentacji: przycisk **Authorize**).
3. Reszta endpointów wymaga zalogowania.

## Model danych
Pozycja stanu to **komponent + właściciel + posiadacz + lokalizacja + ilość**. Właściciel to osoba, do której
należą sztuki, posiadacz to osoba, która fizycznie je ma. Osoby (`/people`) to właściciele i posiadacze;
każdy użytkownik ma przypisaną osobę. Cena jednostkowa pozycji to **średnia ważona** cen zakupu.

## Role
| Rola | Może |
|---|---|
| `admin` | wszystko: użytkownicy, osoby, definicje komponentów |
| `user` | przeglądać oraz zmieniać stany (przyjęcie, przekazanie, zmiana właściciela, zużycie, korekty) i zamówienia |

## Błędy
Błędy mają postać `{"detail": "komunikat"}` (przy 422 `detail` jest listą błędów pól).
`401` brak/nieważny token, `403` brak uprawnień, `404` brak zasobu, `409` konflikt (np. za mało sztuk),
`422` niepoprawne dane, `429` zbyt wiele prób logowania (nagłówek `Retry-After` w sekundach).

## Ochrona przed zgadywaniem haseł
Pierwszy błędny login jest odrzucany od razu, kolejne próby są blokowane na coraz dłużej (1, 2, 4, 8 ... s,
maks. 15 min) osobno dla loginu i adresu IP. W czasie blokady odpowiedź to `429`, także dla poprawnego hasła.
""".strip()

TAGS = [
    {"name": "Uwierzytelnianie", "description": "Logowanie, sesje i własne konto."},
    {"name": "Użytkownicy", "description": "Zarządzanie kontami (tylko admin, poza utworzeniem pierwszego konta)."},
    {"name": "Osoby", "description": "Właściciele i posiadacze komponentów (także osoby bez konta)."},
    {"name": "Komponenty", "description": "Definicje komponentów, sumy sztuk i wartości oraz lista tego, co się kończy."},
    {"name": "Stany", "description": "Aktualne stany i operacje na nich. Każda operacja trafia do historii."},
    {"name": "Historia", "description": "Dziennik wszystkich operacji."},
    {"name": "Zamówienia", "description": "Koszyk zakupów: sugestie z \"kończy się\" → koszyk → zamówione → przyjęte na stan."},
]

# (metoda, ścieżka) -> (tag, podsumowanie, opis)
DOCS: dict[tuple[str, str], tuple[str, str, str]] = {
    ("GET", "/auth/status"): ("Uwierzytelnianie", "Czy istnieją już konta",
        "Publiczny. `initialized=false` oznacza, że pierwszy `POST /users` utworzy administratora.\n\nZwraca `{\"initialized\": bool}`."),
    ("POST", "/auth/login"): ("Uwierzytelnianie", "Logowanie",
        "Zwraca `{token, user}`. Token działa 30 dni (nagłówek `Authorization: Bearer ...`). "
        "Zły login i złe hasło dają ten sam błąd `401`. Po błędach: `429` + `Retry-After`."),
    ("POST", "/auth/logout"): ("Uwierzytelnianie", "Wylogowanie", "Unieważnia bieżący token. Zwraca `{\"ok\": true}`."),
    ("GET", "/auth/me"): ("Uwierzytelnianie", "Zalogowany użytkownik", "Zwraca `{id, username, role, person_id}`."),
    ("POST", "/auth/password"): ("Uwierzytelnianie", "Zmiana własnego hasła",
        "Wymaga starego hasła (`403` gdy błędne; te próby też podlegają blokadzie `429`). Unieważnia wszystkie sesje "
        "i zwraca nowy `{token}`."),

    ("POST", "/users"): ("Użytkownicy", "Utwórz użytkownika",
        "**Gdy nie ma jeszcze żadnego konta:** bez logowania, a konto zawsze zostaje `admin` (pole `role` jest ignorowane). "
        "Potem tylko administrator. Tworzy też osobę magazynową (`person_name`, domyślnie login) albo podpina istniejącą "
        "o tej nazwie. Login nie rozróżnia wielkości liter (`409` gdy zajęty)."),
    ("GET", "/users"): ("Użytkownicy", "Lista użytkowników", "Tylko admin. Zwraca `[{id, username, role, active, person_id, person}]`."),
    ("PATCH", "/users/{user_id}"): ("Użytkownicy", "Zmień hasło, rolę lub aktywność",
        "Tylko admin. Użytkowników się nie usuwa, tylko dezaktywuje (`active=false`), żeby zostali w historii. "
        "Zmiana hasła i dezaktywacja kończą jego sesje. `409` przy odebraniu uprawnień ostatniemu aktywnemu adminowi."),

    ("POST", "/people"): ("Osoby", "Dodaj osobę", "Tylko admin. Osoba bez konta, np. zewnętrzny posiadacz. `409` gdy nazwa zajęta."),
    ("GET", "/people"): ("Osoby", "Lista osób", "Zwraca `[{id, name}]` posortowane po nazwie."),
    ("PATCH", "/people/{person_id}"): ("Osoby", "Zmień nazwę osoby", "Tylko admin. Zmiana widoczna wszędzie, bo stany wskazują osobę po `id`."),

    ("POST", "/components"): ("Komponenty", "Dodaj komponent",
        "Tylko admin. `min_qty` to próg: poniżej tej sumy sztuk komponent trafia na listę \"kończy się\" (0 = bez progu)."),
    ("GET", "/components"): ("Komponenty", "Lista komponentów",
        "Parametr `q` szuka w nazwie i numerze katalogowym. Każdy komponent ma `total_qty` i `total_value` (suma po wszystkich pozycjach)."),
    ("GET", "/components/{component_id}"): ("Komponenty", "Szczegóły komponentu",
        "Komponent z sumami, flagą `low` (poniżej progu) i listą `stock[]`: kto jest właścicielem, kto ma, gdzie, ile i po ile."),
    ("PATCH", "/components/{component_id}"): ("Komponenty", "Edytuj komponent",
        "Tylko admin. Wysyłasz tylko zmieniane pola; `null` czyści `part_number` / `description`."),
    ("GET", "/low-stock"): ("Komponenty", "Co się kończy",
        "Komponenty z `min_qty > 0`, których suma sztuk jest poniżej progu, od najbardziej brakujących. "
        "Pole `shortage` to ile brakuje do progu."),

    ("GET", "/stock"): ("Stany", "Aktualne stany",
        "Pozycje (komponent, właściciel, posiadacz, lokalizacja, ilość, `unit_cost`, `value`). Filtry łączą się warunkiem AND."),
    ("GET", "/locations"): ("Stany", "Lokalizacje", "Używane lokalizacje z liczbą pozycji, sztuk i wartością. Pusta lokalizacja oznacza brak."),
    ("POST", "/stock/receive"): ("Stany", "Przyjmij na stan",
        "Zakup / przyjęcie. Posiadacz domyślnie = właściciel. Gdy taka pozycja już istnieje (ten sam komponent, właściciel, "
        "posiadacz i lokalizacja), ilość się sumuje, a cena staje się **średnią ważoną**."),
    ("POST", "/stock/transfer"): ("Stany", "Przekaż / przenieś",
        "Zmiana posiadacza i/lub lokalizacji; właściciel bez zmian, cena jednostkowa jest przenoszona. Pominięte "
        "`to_holder_id` / `to_location` oznaczają brak zmiany. `409` gdy źródło ma za mało sztuk."),
    ("POST", "/stock/change-owner"): ("Stany", "Zmień właściciela",
        "Sztuki zostają u tego samego posiadacza i w tym samym miejscu, zmienia się tylko właściciel."),
    ("POST", "/stock/consume"): ("Stany", "Zużyj",
        "Zdejmuje ilość ze stanu (`409` przy zbyt małej ilości). Pozycja zerowa jest usuwana."),
    ("PATCH", "/stock/{stock_id}"): ("Stany", "Edytuj pozycję w miejscu",
        "Korekta `qty` (0 usuwa pozycję), `unit_cost` albo zmiana `location` całej pozycji (scala się z istniejącą w "
        "docelowym miejscu). Zwraca `{id, merged, removed}`. Korekty trafiają do historii jako `adjust`."),

    ("GET", "/movements"): ("Historia", "Historia operacji",
        "Najnowsze pierwsze. `kind`: `receive`, `transfer`, `change_owner`, `consume`, `adjust`. "
        "Filtr `component_id`, limit `limit` (maks. 1000, domyślnie 100). Pole `by_user` to autor operacji."),

    ("GET", "/orders"): ("Zamówienia", "Lista pozycji zamówień",
        "Filtr `status`: `cart` (koszyk), `ordered` (zamówione, w drodze), `received` (przyjęte). Zawiera `in_stock` i `min_qty` komponentu."),
    ("GET", "/orders/summary"): ("Zamówienia", "Podsumowanie",
        "Zwraca `{cart, ordered: {items, qty, value}, suggestions}`; używane w pasku statusu."),
    ("GET", "/orders/suggestions"): ("Zamówienia", "Sugestie z listy \"kończy się\"",
        "Brakująca ilość do progu pomniejszona o to, co już jest w koszyku lub zamówione (`on_order`). "
        "Proponowana cena to średnia z magazynu albo cena ostatniego przyjęcia."),
    ("POST", "/orders/suggestions/add"): ("Zamówienia", "Dodaj sugestie do koszyka",
        "Wrzuca wszystkie sugestie (albo tylko komponenty z `ids`) w proponowanej ilości. Zwraca `{added}`."),
    ("POST", "/orders/mark-ordered"): ("Zamówienia", "Oznacz koszyk jako zamówiony",
        "Wszystkie pozycje koszyka albo tylko wskazane `ids`. Zwraca `{updated}`."),
    ("POST", "/orders"): ("Zamówienia", "Dodaj do koszyka",
        "Ta sama pozycja już w koszyku scala się (sumuje ilość)."),
    ("PATCH", "/orders/{order_id}"): ("Zamówienia", "Edytuj pozycję zamówienia",
        "Ilość, cena, dostawca, notatka oraz `status` (`cart` ↔ `ordered`). `409` dla pozycji już przyjętej."),
    ("DELETE", "/orders/{order_id}"): ("Zamówienia", "Usuń pozycję zamówienia", "Usuwa pozycję trwale."),
    ("POST", "/orders/{order_id}/receive"): ("Zamówienia", "Przyjmij dostawę na stan",
        "Dodaje towar do magazynu jak `/stock/receive` (z notatką \"Zamówienie #N\") i oznacza pozycję jako przyjętą. "
        "`qty` to faktycznie dostarczona ilość (domyślnie jak w zamówieniu)."),
}

# przykładowe ciała żądań (pokazywane w /docs przy "Try it out")
EXAMPLES: dict[tuple[str, str], dict] = {
    ("POST", "/auth/login"): {"username": "jan", "password": "haslo1234"},
    ("POST", "/auth/password"): {"old_password": "haslo1234", "new_password": "noweHaslo5678"},
    ("POST", "/users"): {"username": "ola", "password": "haslo5678", "role": "user", "person_name": "Ola K."},
    ("PATCH", "/users/{user_id}"): {"role": "admin"},
    ("POST", "/people"): {"name": "Magazyn zewnętrzny"},
    ("PATCH", "/people/{person_id}"): {"name": "Ola Kowalska"},
    ("POST", "/components"): {"name": "Rezystor 10k", "part_number": "R10K", "description": "0805, 1%", "min_qty": 200},
    ("PATCH", "/components/{component_id}"): {"min_qty": 300},
    ("POST", "/stock/receive"): {"component_id": 1, "owner_id": 1, "holder_id": 2, "location": "Szafa A/2",
                                 "qty": 100, "unit_cost": 0.1, "note": "dostawa z hurtowni"},
    ("POST", "/stock/transfer"): {"component_id": 1, "owner_id": 1, "from_holder_id": 1, "from_location": "Szafa A/2",
                                  "to_holder_id": 2, "to_location": "Biurko", "qty": 30, "note": "do projektu"},
    ("POST", "/stock/change-owner"): {"component_id": 1, "from_owner_id": 1, "to_owner_id": 2, "holder_id": 2,
                                      "location": "Biurko", "qty": 10},
    ("POST", "/stock/consume"): {"component_id": 1, "owner_id": 1, "holder_id": 2, "location": "Biurko", "qty": 5,
                                 "note": "zlutowane"},
    ("PATCH", "/stock/{stock_id}"): {"qty": 95},
    ("POST", "/orders/suggestions/add"): {"ids": [1, 3]},
    ("POST", "/orders/mark-ordered"): {"ids": [4, 5]},
    ("POST", "/orders"): {"component_id": 1, "qty": 200, "unit_cost": 0.09, "supplier": "TME", "note": "pilne"},
    ("PATCH", "/orders/{order_id}"): {"qty": 250, "status": "ordered"},
    ("POST", "/orders/{order_id}/receive"): {"owner_id": 1, "holder_id": 1, "location": "Szafa A/2", "qty": 200,
                                             "unit_cost": 0.09},
}

ERRORS = {
    401: "Brak lub nieważny token",
    403: "Brak uprawnień (wymagany administrator)",
    404: "Nie znaleziono zasobu",
    409: "Konflikt (np. za mało sztuk, duplikat nazwy, ostatni administrator)",
    429: "Zbyt wiele prób, spróbuj po czasie z nagłówka Retry-After",
}


# opisy pól i parametrów dopisywane do specyfikacji (klucz: (model, pole) ma pierwszeństwo przed samą nazwą pola)
FIELD_DOCS = {
    "component_id": "ID komponentu", "owner_id": "ID osoby będącej właścicielem",
    "holder_id": "ID osoby, która fizycznie ma sztuki",
    ("ReceiveIn", "holder_id"): "ID osoby, która ma sztuki (domyślnie = właściciel)",
    ("OrderReceiveIn", "holder_id"): "ID posiadacza (domyślnie = właściciel)",
    "location": "Lokalizacja, wolny tekst, np. \"Szafa A/2\" (pusta = brak)",
    "qty": "Ilość sztuk (> 0)", ("StockPatch", "qty"): "Nowa ilość pozycji; 0 usuwa pozycję",
    ("OrderPatch", "qty"): "Nowa zamawiana ilość (> 0)",
    "unit_cost": "Cena za sztukę", ("StockPatch", "unit_cost"): "Nowa cena jednostkowa pozycji (null = brak ceny)",
    "note": "Notatka (zapisywana w historii)",
    "from_holder_id": "ID obecnego posiadacza (źródło)", "from_location": "Obecna lokalizacja (źródło)",
    "to_holder_id": "ID nowego posiadacza (pominięte = bez zmiany)", "to_location": "Nowa lokalizacja (pominięte = bez zmiany)",
    "from_owner_id": "ID obecnego właściciela", "to_owner_id": "ID nowego właściciela",
    "min_qty": "Próg alarmowy: poniżej tej sumy sztuk komponent \"się kończy\" (0 = bez progu)",
    "part_number": "Numer katalogowy (unikalny)", "description": "Opis komponentu",
    "name": "Nazwa", ("PersonIn", "name"): "Nazwa osoby (unikalna)", ("ComponentIn", "name"): "Nazwa komponentu",
    ("ComponentPatch", "name"): "Nowa nazwa komponentu",
    "username": "Login (bez rozróżniania wielkości liter, unikalny)", "password": "Hasło (min. 8 znaków)",
    ("LoginIn", "password"): "Hasło", "role": "`admin` lub `user`", "active": "Czy konto jest aktywne",
    "old_password": "Obecne hasło", "new_password": "Nowe hasło (min. 8 znaków)",
    "supplier": "Dostawca", "status": "`cart` (koszyk) lub `ordered` (zamówione)",
    "ids": "Lista ID pozycji; pominięta lub null = wszystkie",
    ("IdsIn", "ids"): "Lista ID (komponentów dla sugestii, pozycji dla mark-ordered); pominięta = wszystkie",
}
PARAM_DOCS = {
    "q": "Szukany fragment nazwy lub numeru katalogowego", "limit": "Maksymalna liczba wyników",
    "component_id": "Filtr: ID komponentu", "status": "Filtr statusu: `cart`, `ordered` lub `received`",
}


def _enhance(spec: dict) -> None:
    for name, schema in spec.get("components", {}).get("schemas", {}).items():
        for prop, p in schema.get("properties", {}).items():
            if not p.get("description"):
                d = FIELD_DOCS.get((name, prop)) or FIELD_DOCS.get(prop)
                if d:
                    p["description"] = d
    for item in spec.get("paths", {}).values():
        for op in item.values():
            for prm in op.get("parameters", []):
                if not prm.get("description") and prm["name"] in PARAM_DOCS:
                    prm["description"] = PARAM_DOCS[prm["name"]]


# operacje, które mogą zwrócić 409
CONFLICT = {("POST", "/users"), ("PATCH", "/users/{user_id}"), ("POST", "/people"), ("PATCH", "/people/{person_id}"),
            ("POST", "/components"), ("PATCH", "/components/{component_id}"), ("POST", "/stock/transfer"),
            ("POST", "/stock/change-owner"), ("POST", "/stock/consume"), ("PATCH", "/orders/{order_id}"),
            ("POST", "/orders/{order_id}/receive")}


def _dep_calls(dependant) -> set:
    calls = set()
    for d in dependant.dependencies:
        if d.call:
            calls.add(d.call)
        calls |= _dep_calls(d)
    return calls


def access_level(route: APIRoute) -> str:
    names = {getattr(c, "__name__", "") for c in _dep_calls(route.dependant)}
    if "require_admin" in names:
        return "admin"
    if "current_user" in names:
        return "zalogowany"
    return "publiczny"


def document(app) -> None:
    """Nakłada opisy na trasy; wywołać po zarejestrowaniu wszystkich endpointów."""
    app.title = "Magazyn: API"
    app.version = "1.0"
    app.description = DESCRIPTION
    app.openapi_tags = TAGS
    if not getattr(app, "_docs_patched", False):   # opisy pól dopisywane przy generowaniu specyfikacji
        original = app.openapi

        def patched():
            if app.openapi_schema:
                return app.openapi_schema
            spec = original()
            _enhance(spec)
            app.openapi_schema = spec
            return spec

        app.openapi = patched
        app._docs_patched = True
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods - {"HEAD", "OPTIONS"}:
            key = (method, route.path)
            if key not in DOCS:
                continue
            tag, summary, desc = DOCS[key]
            level = access_level(route)
            route.tags = [tag]
            route.summary = summary
            route.response_description = "OK"
            access = {"publiczny": "Dostęp: **publiczny**.", "zalogowany": "Dostęp: **zalogowany**.",
                      "admin": "Dostęp: **tylko admin**."}[level]
            if key == ("POST", "/users"):
                access = "Dostęp: **publiczny dla pierwszego konta, potem tylko admin**."
            for prefix in ("Tylko admin. ", "Publiczny. "):
                desc = desc.removeprefix(prefix)
            route.description = f"{access}\n\n{desc}"
            extra = {}
            if level != "publiczny":
                extra[401] = {"description": ERRORS[401]}
            if level == "admin":
                extra[403] = {"description": ERRORS[403]}
            if "{" in route.path:
                extra[404] = {"description": ERRORS[404]}
            if key in CONFLICT:
                extra[409] = {"description": ERRORS[409]}
            if route.path in ("/auth/login", "/auth/password"):
                extra[429] = {"description": ERRORS[429]}
                if route.path == "/auth/login":
                    extra[401] = {"description": "Zły login lub hasło (albo konto nieaktywne)"}
            for code, resp in extra.items():
                route.responses.setdefault(code, resp)
            if key in EXAMPLES:
                route.openapi_extra = {"requestBody": {"content": {"application/json": {"example": EXAMPLES[key]}}}}


# ---------------------------------------------------------------- generator API.md
def _resolve(spec: dict, schema: dict) -> dict:
    ref = schema.get("$ref")
    return spec["components"]["schemas"][ref.split("/")[-1]] if ref else schema


def _type(spec: dict, s: dict) -> str:
    if "anyOf" in s:
        parts = [_type(spec, x) for x in s["anyOf"]]
        return " \\| ".join(p for p in parts if p != "null") + (" (opcjonalne)" if "null" in parts else "")
    if "$ref" in s:
        return s["$ref"].split("/")[-1]
    t = s.get("type", "any")
    if t == "array":
        return f"lista[{_type(spec, s.get('items', {}))}]"
    out = t
    for k, label in (("minimum", "≥"), ("exclusiveMinimum", ">"), ("maximum", "≤")):
        if k in s:
            out += f" {label}{s[k]:g}"
    if "minLength" in s:
        out += f", min. {s['minLength']} zn."
    if "pattern" in s:
        out += f", wzorzec `{s['pattern']}`"
    return out


def _body_table(spec: dict, schema: dict) -> list[str]:
    schema = _resolve(spec, schema)
    req = set(schema.get("required", []))
    lines = ["| Pole | Typ | Wymagane | Opis |", "|---|---|---|---|"]
    for name, p in schema.get("properties", {}).items():
        desc = (p.get("description") or "").replace("\n", " ")
        default = f" (domyślnie `{json.dumps(p['default'], ensure_ascii=False)}`)" if "default" in p and p["default"] is not None else ""
        lines.append(f"| `{name}` | {_type(spec, p)} | {'tak' if name in req else 'nie'} | {desc}{default} |")
    return lines


def markdown(app) -> str:
    document(app)
    app.openapi_schema = None
    spec = app.openapi()
    routes = {(m, r.path): r for r in app.routes if isinstance(r, APIRoute) for m in r.methods - {"HEAD", "OPTIONS"}}
    out = [f"# {spec['info']['title']}", "", "> Plik generowany: `python apidocs.py`. Interaktywna wersja działa pod `/docs` (Swagger UI) "
           "i `/redoc`, surowa specyfikacja pod `/openapi.json`.", "", spec["info"]["description"], ""]
    by_tag: dict[str, list] = {}
    for path, item in spec["paths"].items():
        for method, op in item.items():
            by_tag.setdefault(op["tags"][0], []).append((path, method.upper(), op))
    for tag in TAGS:
        ops = by_tag.get(tag["name"], [])
        if not ops:
            continue
        out += [f"## {tag['name']}", "", tag["description"], ""]
        for path, method, op in ops:
            out += [f"### `{method} {path}`: {op['summary']}", "", op.get("description", ""), ""]
            params = op.get("parameters", [])
            if params:
                out += ["**Parametry**", "", "| Nazwa | Gdzie | Typ | Wymagany | Opis |", "|---|---|---|---|---|"]
                for p in params:
                    out.append(f"| `{p['name']}` | {p['in']} | {_type(spec, p.get('schema', {}))} | "
                               f"{'tak' if p.get('required') else 'nie'} | {(p.get('description') or '').replace(chr(10), ' ')} |")
                out.append("")
            body = op.get("requestBody", {}).get("content", {}).get("application/json")
            if body:
                out += ["**Ciało żądania** (JSON)", ""] + _body_table(spec, body["schema"]) + [""]
                if "example" in body:
                    out += ["```json", json.dumps(body["example"], ensure_ascii=False, indent=2), "```", ""]
            res = [f"`{c}` {r.get('description', '')}".rstrip() for c, r in op["responses"].items() if c != "422"]
            if "422" in op["responses"]:
                res.append("`422` niepoprawne dane")
            out += ["**Odpowiedzi:** " + ", ".join(res), ""]
    return "\n".join(out).rstrip() + "\n"


if __name__ == "__main__":
    import os
    import tempfile

    os.environ.setdefault("MAGAZYN_DB", os.path.join(tempfile.mkdtemp(), "docs.db"))
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import main

    target = os.path.join(os.path.dirname(os.path.abspath(__file__)), "API.md")
    with open(target, "w", encoding="utf-8") as f:
        f.write(markdown(main.app))
    print("zapisano", target)
