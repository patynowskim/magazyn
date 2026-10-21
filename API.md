# Magazyn: API

> Plik generowany: `python apidocs.py`. Interaktywna wersja działa pod `/docs` (Swagger UI) i `/redoc`, surowa specyfikacja pod `/openapi.json`.

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

## Uwierzytelnianie

Logowanie, sesje i własne konto.

### `GET /auth/status`: Czy istnieją już konta

Dostęp: **publiczny**.

`initialized=false` oznacza, że pierwszy `POST /users` utworzy administratora.

Zwraca `{"initialized": bool}`.

**Odpowiedzi:** `200` OK

### `POST /auth/login`: Logowanie

Dostęp: **publiczny**.

Zwraca `{token, user}`. Token działa 30 dni (nagłówek `Authorization: Bearer ...`). Zły login i złe hasło dają ten sam błąd `401`. Po błędach: `429` + `Retry-After`.

**Ciało żądania** (JSON)

| Pole | Typ | Wymagane | Opis |
|---|---|---|---|
| `username` | string | tak | Login (bez rozróżniania wielkości liter, unikalny) |
| `password` | string | tak | Hasło |

```json
{
  "username": "jan",
  "password": "haslo1234"
}
```

**Odpowiedzi:** `200` OK, `429` Zbyt wiele prób, spróbuj po czasie z nagłówka Retry-After, `401` Zły login lub hasło (albo konto nieaktywne), `422` niepoprawne dane

### `POST /auth/logout`: Wylogowanie

Dostęp: **zalogowany**.

Unieważnia bieżący token. Zwraca `{"ok": true}`.

**Odpowiedzi:** `200` OK, `401` Brak lub nieważny token

### `GET /auth/me`: Zalogowany użytkownik

Dostęp: **zalogowany**.

Zwraca `{id, username, role, person_id}`.

**Odpowiedzi:** `200` OK, `401` Brak lub nieważny token

### `POST /auth/password`: Zmiana własnego hasła

Dostęp: **zalogowany**.

Wymaga starego hasła (`403` gdy błędne; te próby też podlegają blokadzie `429`). Unieważnia wszystkie sesje i zwraca nowy `{token}`.

**Ciało żądania** (JSON)

| Pole | Typ | Wymagane | Opis |
|---|---|---|---|
| `old_password` | string | tak | Obecne hasło |
| `new_password` | string, min. 8 zn. | tak | Nowe hasło (min. 8 znaków) |

```json
{
  "old_password": "haslo1234",
  "new_password": "noweHaslo5678"
}
```

**Odpowiedzi:** `200` OK, `401` Brak lub nieważny token, `429` Zbyt wiele prób, spróbuj po czasie z nagłówka Retry-After, `422` niepoprawne dane

## Użytkownicy

Zarządzanie kontami (tylko admin, poza utworzeniem pierwszego konta).

### `GET /users`: Lista użytkowników

Dostęp: **tylko admin**.

Zwraca `[{id, username, role, active, person_id, person}]`.

**Odpowiedzi:** `200` OK, `401` Brak lub nieważny token, `403` Brak uprawnień (wymagany administrator)

### `POST /users`: Utwórz użytkownika

Dostęp: **publiczny dla pierwszego konta, potem tylko admin**.

**Gdy nie ma jeszcze żadnego konta:** bez logowania, a konto zawsze zostaje `admin` (pole `role` jest ignorowane). Potem tylko administrator. Tworzy też osobę magazynową (`person_name`, domyślnie login) albo podpina istniejącą o tej nazwie. Login nie rozróżnia wielkości liter (`409` gdy zajęty).

**Ciało żądania** (JSON)

| Pole | Typ | Wymagane | Opis |
|---|---|---|---|
| `username` | string, min. 1 zn. | tak | Login (bez rozróżniania wielkości liter, unikalny) |
| `password` | string, min. 8 zn. | tak | Hasło (min. 8 znaków) |
| `role` | string, wzorzec `^(admin|user)$` | nie | `admin` lub `user` (domyślnie `"user"`) |
| `person_name` | string (opcjonalne) | nie | nazwa osoby w magazynie; domyślnie = username |

```json
{
  "username": "ola",
  "password": "haslo5678",
  "role": "user",
  "person_name": "Ola K."
}
```

**Odpowiedzi:** `201` OK, `409` Konflikt (np. za mało sztuk, duplikat nazwy, ostatni administrator), `422` niepoprawne dane

### `PATCH /users/{user_id}`: Zmień hasło, rolę lub aktywność

Dostęp: **tylko admin**.

Użytkowników się nie usuwa, tylko dezaktywuje (`active=false`), żeby zostali w historii. Zmiana hasła i dezaktywacja kończą jego sesje. `409` przy odebraniu uprawnień ostatniemu aktywnemu adminowi.

**Parametry**

| Nazwa | Gdzie | Typ | Wymagany | Opis |
|---|---|---|---|---|
| `user_id` | path | integer | tak |  |

**Ciało żądania** (JSON)

| Pole | Typ | Wymagane | Opis |
|---|---|---|---|
| `password` | string, min. 8 zn. (opcjonalne) | nie | Hasło (min. 8 znaków) |
| `role` | string, wzorzec `^(admin|user)$` (opcjonalne) | nie | `admin` lub `user` |
| `active` | boolean (opcjonalne) | nie | Czy konto jest aktywne |

```json
{
  "role": "admin"
}
```

**Odpowiedzi:** `200` OK, `401` Brak lub nieważny token, `403` Brak uprawnień (wymagany administrator), `404` Nie znaleziono zasobu, `409` Konflikt (np. za mało sztuk, duplikat nazwy, ostatni administrator), `422` niepoprawne dane

## Osoby

Właściciele i posiadacze komponentów (także osoby bez konta).

### `GET /people`: Lista osób

Dostęp: **zalogowany**.

Zwraca `[{id, name}]` posortowane po nazwie.

**Odpowiedzi:** `200` OK, `401` Brak lub nieważny token

### `POST /people`: Dodaj osobę

Dostęp: **tylko admin**.

Osoba bez konta, np. zewnętrzny posiadacz. `409` gdy nazwa zajęta.

**Ciało żądania** (JSON)

| Pole | Typ | Wymagane | Opis |
|---|---|---|---|
| `name` | string, min. 1 zn. | tak | Nazwa osoby (unikalna) |

```json
{
  "name": "Magazyn zewnętrzny"
}
```

**Odpowiedzi:** `201` OK, `401` Brak lub nieważny token, `403` Brak uprawnień (wymagany administrator), `409` Konflikt (np. za mało sztuk, duplikat nazwy, ostatni administrator), `422` niepoprawne dane

### `PATCH /people/{person_id}`: Zmień nazwę osoby

Dostęp: **tylko admin**.

Zmiana widoczna wszędzie, bo stany wskazują osobę po `id`.

**Parametry**

| Nazwa | Gdzie | Typ | Wymagany | Opis |
|---|---|---|---|---|
| `person_id` | path | integer | tak |  |

**Ciało żądania** (JSON)

| Pole | Typ | Wymagane | Opis |
|---|---|---|---|
| `name` | string, min. 1 zn. | tak | Nazwa osoby (unikalna) |

```json
{
  "name": "Ola Kowalska"
}
```

**Odpowiedzi:** `200` OK, `401` Brak lub nieważny token, `403` Brak uprawnień (wymagany administrator), `404` Nie znaleziono zasobu, `409` Konflikt (np. za mało sztuk, duplikat nazwy, ostatni administrator), `422` niepoprawne dane

## Komponenty

Definicje komponentów, sumy sztuk i wartości oraz lista tego, co się kończy.

### `POST /components`: Dodaj komponent

Dostęp: **tylko admin**.

`min_qty` to próg: poniżej tej sumy sztuk komponent trafia na listę "kończy się" (0 = bez progu).

**Ciało żądania** (JSON)

| Pole | Typ | Wymagane | Opis |
|---|---|---|---|
| `name` | string, min. 1 zn. | tak | Nazwa komponentu |
| `part_number` | string (opcjonalne) | nie | Numer katalogowy (unikalny) |
| `description` | string (opcjonalne) | nie | Opis komponentu |
| `min_qty` | integer ≥0 | nie | próg alarmowy (suma sztuk) (domyślnie `0`) |

```json
{
  "name": "Rezystor 10k",
  "part_number": "R10K",
  "description": "0805, 1%",
  "min_qty": 200
}
```

**Odpowiedzi:** `201` OK, `401` Brak lub nieważny token, `403` Brak uprawnień (wymagany administrator), `409` Konflikt (np. za mało sztuk, duplikat nazwy, ostatni administrator), `422` niepoprawne dane

### `GET /components`: Lista komponentów

Dostęp: **zalogowany**.

Parametr `q` szuka w nazwie i numerze katalogowym. Każdy komponent ma `total_qty` i `total_value` (suma po wszystkich pozycjach).

**Parametry**

| Nazwa | Gdzie | Typ | Wymagany | Opis |
|---|---|---|---|---|
| `q` | query | string (opcjonalne) | nie | Szukany fragment nazwy lub numeru katalogowego |

**Odpowiedzi:** `200` OK, `401` Brak lub nieważny token, `422` niepoprawne dane

### `PATCH /components/{component_id}`: Edytuj komponent

Dostęp: **tylko admin**.

Wysyłasz tylko zmieniane pola; `null` czyści `part_number` / `description`.

**Parametry**

| Nazwa | Gdzie | Typ | Wymagany | Opis |
|---|---|---|---|---|
| `component_id` | path | integer | tak | Filtr: ID komponentu |

**Ciało żądania** (JSON)

| Pole | Typ | Wymagane | Opis |
|---|---|---|---|
| `name` | string, min. 1 zn. (opcjonalne) | nie | Nowa nazwa komponentu |
| `part_number` | string (opcjonalne) | nie | Numer katalogowy (unikalny) |
| `description` | string (opcjonalne) | nie | Opis komponentu |
| `min_qty` | integer ≥0 (opcjonalne) | nie | Próg alarmowy: poniżej tej sumy sztuk komponent "się kończy" (0 = bez progu) |

```json
{
  "min_qty": 300
}
```

**Odpowiedzi:** `200` OK, `401` Brak lub nieważny token, `403` Brak uprawnień (wymagany administrator), `404` Nie znaleziono zasobu, `409` Konflikt (np. za mało sztuk, duplikat nazwy, ostatni administrator), `422` niepoprawne dane

### `GET /components/{component_id}`: Szczegóły komponentu

Dostęp: **zalogowany**.

Komponent z sumami, flagą `low` (poniżej progu) i listą `stock[]`: kto jest właścicielem, kto ma, gdzie, ile i po ile.

**Parametry**

| Nazwa | Gdzie | Typ | Wymagany | Opis |
|---|---|---|---|---|
| `component_id` | path | integer | tak | Filtr: ID komponentu |

**Odpowiedzi:** `200` OK, `401` Brak lub nieważny token, `404` Nie znaleziono zasobu, `422` niepoprawne dane

### `GET /low-stock`: Co się kończy

Dostęp: **zalogowany**.

Komponenty z `min_qty > 0`, których suma sztuk jest poniżej progu, od najbardziej brakujących. Pole `shortage` to ile brakuje do progu.

**Odpowiedzi:** `200` OK, `401` Brak lub nieważny token

## Stany

Aktualne stany i operacje na nich. Każda operacja trafia do historii.

### `GET /stock`: Aktualne stany

Dostęp: **zalogowany**.

Pozycje (komponent, właściciel, posiadacz, lokalizacja, ilość, `unit_cost`, `value`). Filtry łączą się warunkiem AND.

**Parametry**

| Nazwa | Gdzie | Typ | Wymagany | Opis |
|---|---|---|---|---|
| `component_id` | query | integer (opcjonalne) | nie | Filtr: ID komponentu |
| `owner_id` | query | integer (opcjonalne) | nie | filtruj po właścicielu |
| `holder_id` | query | integer (opcjonalne) | nie | filtruj po posiadaczu |
| `location` | query | string (opcjonalne) | nie | fragment nazwy lokalizacji |

**Odpowiedzi:** `200` OK, `401` Brak lub nieważny token, `422` niepoprawne dane

### `GET /locations`: Lokalizacje

Dostęp: **zalogowany**.

Używane lokalizacje z liczbą pozycji, sztuk i wartością. Pusta lokalizacja oznacza brak.

**Odpowiedzi:** `200` OK, `401` Brak lub nieważny token

### `PATCH /stock/{stock_id}`: Edytuj pozycję w miejscu

Dostęp: **zalogowany**.

Korekta `qty` (0 usuwa pozycję), `unit_cost` albo zmiana `location` całej pozycji (scala się z istniejącą w docelowym miejscu). Zwraca `{id, merged, removed}`. Korekty trafiają do historii jako `adjust`.

**Parametry**

| Nazwa | Gdzie | Typ | Wymagany | Opis |
|---|---|---|---|---|
| `stock_id` | path | integer | tak |  |

**Ciało żądania** (JSON)

| Pole | Typ | Wymagane | Opis |
|---|---|---|---|
| `qty` | integer ≥0 (opcjonalne) | nie | 0 = usuń pozycję |
| `unit_cost` | number ≥0 (opcjonalne) | nie | Nowa cena jednostkowa pozycji (null = brak ceny) |
| `location` | string (opcjonalne) | nie | Lokalizacja, wolny tekst, np. "Szafa A/2" (pusta = brak) |

```json
{
  "qty": 95
}
```

**Odpowiedzi:** `200` OK, `401` Brak lub nieważny token, `404` Nie znaleziono zasobu, `422` niepoprawne dane

### `POST /stock/receive`: Przyjmij na stan

Dostęp: **zalogowany**.

Zakup / przyjęcie. Posiadacz domyślnie = właściciel. Gdy taka pozycja już istnieje (ten sam komponent, właściciel, posiadacz i lokalizacja), ilość się sumuje, a cena staje się **średnią ważoną**.

**Ciało żądania** (JSON)

| Pole | Typ | Wymagane | Opis |
|---|---|---|---|
| `component_id` | integer | tak | ID komponentu |
| `owner_id` | integer | tak | ID osoby będącej właścicielem |
| `holder_id` | integer (opcjonalne) | nie | ID osoby, która ma sztuki (domyślnie = właściciel) |
| `location` | string | nie | Lokalizacja, wolny tekst, np. "Szafa A/2" (pusta = brak) (domyślnie `""`) |
| `qty` | integer >0 | tak | Ilość sztuk (> 0) |
| `unit_cost` | number ≥0 (opcjonalne) | nie | cena zakupu za sztukę |
| `note` | string (opcjonalne) | nie | Notatka (zapisywana w historii) |

```json
{
  "component_id": 1,
  "owner_id": 1,
  "holder_id": 2,
  "location": "Szafa A/2",
  "qty": 100,
  "unit_cost": 0.1,
  "note": "dostawa z hurtowni"
}
```

**Odpowiedzi:** `201` OK, `401` Brak lub nieważny token, `422` niepoprawne dane

### `POST /stock/transfer`: Przekaż / przenieś

Dostęp: **zalogowany**.

Zmiana posiadacza i/lub lokalizacji; właściciel bez zmian, cena jednostkowa jest przenoszona. Pominięte `to_holder_id` / `to_location` oznaczają brak zmiany. `409` gdy źródło ma za mało sztuk.

**Ciało żądania** (JSON)

| Pole | Typ | Wymagane | Opis |
|---|---|---|---|
| `component_id` | integer | tak | ID komponentu |
| `owner_id` | integer | tak | ID osoby będącej właścicielem |
| `from_holder_id` | integer | tak | ID obecnego posiadacza (źródło) |
| `from_location` | string | nie | Obecna lokalizacja (źródło) (domyślnie `""`) |
| `to_holder_id` | integer (opcjonalne) | nie | ID nowego posiadacza (pominięte = bez zmiany) |
| `to_location` | string (opcjonalne) | nie | Nowa lokalizacja (pominięte = bez zmiany) |
| `qty` | integer >0 | tak | Ilość sztuk (> 0) |
| `note` | string (opcjonalne) | nie | Notatka (zapisywana w historii) |

```json
{
  "component_id": 1,
  "owner_id": 1,
  "from_holder_id": 1,
  "from_location": "Szafa A/2",
  "to_holder_id": 2,
  "to_location": "Biurko",
  "qty": 30,
  "note": "do projektu"
}
```

**Odpowiedzi:** `200` OK, `401` Brak lub nieważny token, `409` Konflikt (np. za mało sztuk, duplikat nazwy, ostatni administrator), `422` niepoprawne dane

### `POST /stock/change-owner`: Zmień właściciela

Dostęp: **zalogowany**.

Sztuki zostają u tego samego posiadacza i w tym samym miejscu, zmienia się tylko właściciel.

**Ciało żądania** (JSON)

| Pole | Typ | Wymagane | Opis |
|---|---|---|---|
| `component_id` | integer | tak | ID komponentu |
| `from_owner_id` | integer | tak | ID obecnego właściciela |
| `to_owner_id` | integer | tak | ID nowego właściciela |
| `holder_id` | integer | tak | ID osoby, która fizycznie ma sztuki |
| `location` | string | nie | Lokalizacja, wolny tekst, np. "Szafa A/2" (pusta = brak) (domyślnie `""`) |
| `qty` | integer >0 | tak | Ilość sztuk (> 0) |
| `note` | string (opcjonalne) | nie | Notatka (zapisywana w historii) |

```json
{
  "component_id": 1,
  "from_owner_id": 1,
  "to_owner_id": 2,
  "holder_id": 2,
  "location": "Biurko",
  "qty": 10
}
```

**Odpowiedzi:** `200` OK, `401` Brak lub nieważny token, `409` Konflikt (np. za mało sztuk, duplikat nazwy, ostatni administrator), `422` niepoprawne dane

### `POST /stock/consume`: Zużyj

Dostęp: **zalogowany**.

Zdejmuje ilość ze stanu (`409` przy zbyt małej ilości). Pozycja zerowa jest usuwana.

**Ciało żądania** (JSON)

| Pole | Typ | Wymagane | Opis |
|---|---|---|---|
| `component_id` | integer | tak | ID komponentu |
| `owner_id` | integer | tak | ID osoby będącej właścicielem |
| `holder_id` | integer | tak | ID osoby, która fizycznie ma sztuki |
| `location` | string | nie | Lokalizacja, wolny tekst, np. "Szafa A/2" (pusta = brak) (domyślnie `""`) |
| `qty` | integer >0 | tak | Ilość sztuk (> 0) |
| `note` | string (opcjonalne) | nie | Notatka (zapisywana w historii) |

```json
{
  "component_id": 1,
  "owner_id": 1,
  "holder_id": 2,
  "location": "Biurko",
  "qty": 5,
  "note": "zlutowane"
}
```

**Odpowiedzi:** `200` OK, `401` Brak lub nieważny token, `409` Konflikt (np. za mało sztuk, duplikat nazwy, ostatni administrator), `422` niepoprawne dane

## Historia

Dziennik wszystkich operacji.

### `GET /movements`: Historia operacji

Dostęp: **zalogowany**.

Najnowsze pierwsze. `kind`: `receive`, `transfer`, `change_owner`, `consume`, `adjust`. Filtr `component_id`, limit `limit` (maks. 1000, domyślnie 100). Pole `by_user` to autor operacji.

**Parametry**

| Nazwa | Gdzie | Typ | Wymagany | Opis |
|---|---|---|---|---|
| `component_id` | query | integer (opcjonalne) | nie | Filtr: ID komponentu |
| `limit` | query | integer ≤1000 | nie | Maksymalna liczba wyników |

**Odpowiedzi:** `200` OK, `401` Brak lub nieważny token, `422` niepoprawne dane

## Zamówienia

Koszyk zakupów: sugestie z "kończy się" → koszyk → zamówione → przyjęte na stan.

### `GET /orders`: Lista pozycji zamówień

Dostęp: **zalogowany**.

Filtr `status`: `cart` (koszyk), `ordered` (zamówione, w drodze), `received` (przyjęte). Zawiera `in_stock` i `min_qty` komponentu.

**Parametry**

| Nazwa | Gdzie | Typ | Wymagany | Opis |
|---|---|---|---|---|
| `status` | query | string, wzorzec `^(cart|ordered|received)$` (opcjonalne) | nie | Filtr statusu: `cart`, `ordered` lub `received` |
| `limit` | query | integer ≤2000 | nie | Maksymalna liczba wyników |

**Odpowiedzi:** `200` OK, `401` Brak lub nieważny token, `422` niepoprawne dane

### `POST /orders`: Dodaj do koszyka

Dostęp: **zalogowany**.

Ta sama pozycja już w koszyku scala się (sumuje ilość).

**Ciało żądania** (JSON)

| Pole | Typ | Wymagane | Opis |
|---|---|---|---|
| `component_id` | integer | tak | ID komponentu |
| `qty` | integer >0 | tak | Ilość sztuk (> 0) |
| `unit_cost` | number ≥0 (opcjonalne) | nie | Cena za sztukę |
| `supplier` | string (opcjonalne) | nie | Dostawca |
| `note` | string (opcjonalne) | nie | Notatka (zapisywana w historii) |

```json
{
  "component_id": 1,
  "qty": 200,
  "unit_cost": 0.09,
  "supplier": "TME",
  "note": "pilne"
}
```

**Odpowiedzi:** `201` OK, `401` Brak lub nieważny token, `422` niepoprawne dane

### `GET /orders/summary`: Podsumowanie

Dostęp: **zalogowany**.

Zwraca `{cart, ordered: {items, qty, value}, suggestions}`; używane w pasku statusu.

**Odpowiedzi:** `200` OK, `401` Brak lub nieważny token

### `GET /orders/suggestions`: Sugestie z listy "kończy się"

Dostęp: **zalogowany**.

Brakująca ilość do progu pomniejszona o to, co już jest w koszyku lub zamówione (`on_order`). Proponowana cena to średnia z magazynu albo cena ostatniego przyjęcia.

**Odpowiedzi:** `200` OK, `401` Brak lub nieważny token

### `POST /orders/suggestions/add`: Dodaj sugestie do koszyka

Dostęp: **zalogowany**.

Wrzuca wszystkie sugestie (albo tylko komponenty z `ids`) w proponowanej ilości. Zwraca `{added}`.

**Ciało żądania** (JSON)

| Pole | Typ | Wymagane | Opis |
|---|---|---|---|
| `ids` | lista[integer] (opcjonalne) | nie | Lista ID (komponentów dla sugestii, pozycji dla mark-ordered); pominięta = wszystkie |

```json
{
  "ids": [
    1,
    3
  ]
}
```

**Odpowiedzi:** `200` OK, `401` Brak lub nieważny token, `422` niepoprawne dane

### `POST /orders/mark-ordered`: Oznacz koszyk jako zamówiony

Dostęp: **publiczny**.

Wszystkie pozycje koszyka albo tylko wskazane `ids`. Zwraca `{updated}`.

**Ciało żądania** (JSON)

| Pole | Typ | Wymagane | Opis |
|---|---|---|---|
| `ids` | lista[integer] (opcjonalne) | nie | Lista ID (komponentów dla sugestii, pozycji dla mark-ordered); pominięta = wszystkie |

```json
{
  "ids": [
    4,
    5
  ]
}
```

**Odpowiedzi:** `200` OK, `422` niepoprawne dane

### `PATCH /orders/{order_id}`: Edytuj pozycję zamówienia

Dostęp: **zalogowany**.

Ilość, cena, dostawca, notatka oraz `status` (`cart` ↔ `ordered`). `409` dla pozycji już przyjętej.

**Parametry**

| Nazwa | Gdzie | Typ | Wymagany | Opis |
|---|---|---|---|---|
| `order_id` | path | integer | tak |  |

**Ciało żądania** (JSON)

| Pole | Typ | Wymagane | Opis |
|---|---|---|---|
| `qty` | integer >0 (opcjonalne) | nie | Nowa zamawiana ilość (> 0) |
| `unit_cost` | number ≥0 (opcjonalne) | nie | Cena za sztukę |
| `supplier` | string (opcjonalne) | nie | Dostawca |
| `note` | string (opcjonalne) | nie | Notatka (zapisywana w historii) |
| `status` | string, wzorzec `^(cart|ordered)$` (opcjonalne) | nie | `cart` (koszyk) lub `ordered` (zamówione) |

```json
{
  "qty": 250,
  "status": "ordered"
}
```

**Odpowiedzi:** `200` OK, `401` Brak lub nieważny token, `404` Nie znaleziono zasobu, `409` Konflikt (np. za mało sztuk, duplikat nazwy, ostatni administrator), `422` niepoprawne dane

### `DELETE /orders/{order_id}`: Usuń pozycję zamówienia

Dostęp: **publiczny**.

Usuwa pozycję trwale.

**Parametry**

| Nazwa | Gdzie | Typ | Wymagany | Opis |
|---|---|---|---|---|
| `order_id` | path | integer | tak |  |

**Odpowiedzi:** `200` OK, `404` Nie znaleziono zasobu, `422` niepoprawne dane

### `POST /orders/{order_id}/receive`: Przyjmij dostawę na stan

Dostęp: **zalogowany**.

Dodaje towar do magazynu jak `/stock/receive` (z notatką "Zamówienie #N") i oznacza pozycję jako przyjętą. `qty` to faktycznie dostarczona ilość (domyślnie jak w zamówieniu).

**Parametry**

| Nazwa | Gdzie | Typ | Wymagany | Opis |
|---|---|---|---|---|
| `order_id` | path | integer | tak |  |

**Ciało żądania** (JSON)

| Pole | Typ | Wymagane | Opis |
|---|---|---|---|
| `owner_id` | integer | tak | ID osoby będącej właścicielem |
| `holder_id` | integer (opcjonalne) | nie | ID posiadacza (domyślnie = właściciel) |
| `location` | string | nie | Lokalizacja, wolny tekst, np. "Szafa A/2" (pusta = brak) (domyślnie `""`) |
| `qty` | integer >0 (opcjonalne) | nie | faktycznie dostarczona ilość (domyślnie jak w zamówieniu) |
| `unit_cost` | number ≥0 (opcjonalne) | nie | Cena za sztukę |

```json
{
  "owner_id": 1,
  "holder_id": 1,
  "location": "Szafa A/2",
  "qty": 200,
  "unit_cost": 0.09
}
```

**Odpowiedzi:** `200` OK, `401` Brak lub nieważny token, `404` Nie znaleziono zasobu, `409` Konflikt (np. za mało sztuk, duplikat nazwy, ostatni administrator), `422` niepoprawne dane
