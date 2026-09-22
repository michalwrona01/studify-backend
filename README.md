# Studify backend

## Logowanie i subskrypcje kalendarza

Strona główna `/` wymaga logowania nazwą użytkownika i hasłem przez `/login`.
Nie przyjmuje tokenu do logowania. Po zalogowaniu pokazuje linki subskrypcji
z indywidualnym tokenem użytkownika. Sesja przeglądarki jest zapisana w podpisanym
ciasteczku HttpOnly, ważnym przez 7 dni (odświeżanym przy korzystaniu ze strony).

### Konfiguracja i migracja

Ustaw `SESSION_SECRET_KEY` na losowy sekret (co najmniej 32 znaki) w środowisku
aplikacji lub w Vault `backend/app`. Wszystkie instancje muszą używać tego samego
sekretu; jego zmiana wylogowuje użytkowników. Przykładowe generowanie:

```sh
python -c 'import secrets; print(secrets.token_urlsafe(48))'
```

Domyślnie `SESSION_COOKIE_SECURE=true` wymaga HTTPS. Wyłącznie przy lokalnym
uruchomieniu po HTTP ustaw `SESSION_COOKIE_SECURE=false`.
Zastosuj migrację i uruchom ponownie aplikację:

```sh
poetry run alembic upgrade head
```

Poprzednie ustawienie `CALENDAR_SUBSCRIPTION_TOKEN` nie jest już używane.
Dotychczasowe subskrypcje trzeba zastąpić osobistymi linkami ze strony.

### Dodawanie użytkowników

Uruchom z katalogu repozytorium, z tą samą konfiguracją bazy/Vault co aplikacja:

```sh
poetry run python -m scripts.create_user michal --section 1
```

Skrypt pyta o hasło i jego potwierdzenie, bez wyświetlania wpisywanych znaków.
Hasło musi mieć 12–1024 znaki. Nazwy użytkowników rozróżniają wielkość liter.
Hasła są przechowywane jako PBKDF2-HMAC-SHA256 (600 000 iteracji, losowa sól).
Sekcja musi istnieć w aktualnym planie zajęć. Skrypt automatycznie generuje losowy 256-bitowy token dla nowego użytkownika;
baza wymusza unikalność nazwy i tokenu. Nie ma publicznej rejestracji. Konta może również tworzyć administrator w panelu.

### Subskrypcje

Po zalogowaniu wybierz rodzaj zajęć dla przypisanej sekcji. Link ma postać:

```text
webcal://<host>/api/plan_zajec_lekarski_as.ics?section=1&events_type=inperson&token=<osobisty-token>
```

Dla zajęć online używane jest `events_type=online`. Pobieranie ICS wymaga wyłącznie
osobistego tokenu, bez ciasteczka sesji. Brak lub błędny token zwraca HTTP 401.
Wylogowanie z przeglądarki nie przerywa subskrypcji; usunięcie użytkownika z bazy
unieważnia jego token oraz dostęp przez sesję.

Token jest sekretem dającym dostęp do kalendarza. Jest przechowywany w bazie,
aby zalogowany użytkownik mógł ponownie pobrać swój link. Używaj HTTPS i wyłącz
zapisywanie query string w logach dostępu serwera oraz reverse proxy.

### Testy

Testy używają SQLite w pamięci i nie wymagają Vault, PostgreSQL ani SMTP:

```sh
poetry run python -m unittest discover -s tests/app -p 'test*.py' -v
```

## Panel administratora i przypisanie sekcji

Po migracji `poetry run alembic upgrade head` utwórz pierwsze konto administratora:

```sh
poetry run python -m scripts.create_user admin --section 1 --admin
```

Zaloguj się tym kontem i otwórz `/admin` (link **Użytkownicy** w menu).
Panel pozwala dodawać, edytować i usuwać konta, zmieniać role oraz przypisywać
jedną sekcję z aktualnego planu. Formularz ma pojedynczy wybór sekcji; przypisanie
wielu sekcji lub sekcji nieistniejącej jest odrzucane przez serwer.
Nowe konta zawsze otrzymują własny token subskrypcji.

Przy edycji puste pole hasła zachowuje obecne hasło. Zmiana hasła unieważnia
poprzednie sesje użytkownika (administrator edytujący siebie zachowuje bieżącą
sesję). Usunięcie wymaga potwierdzenia na osobnym ekranie; usuwa także dostęp
przez dotychczasowy token. Nie można usunąć własnego konta ani odebrać sobie
roli administratora. Panel i wszystkie jego operacje wymagają roli administratora;
formularze są chronione przed CSRF.

Dotychczasowi użytkownicy po migracji nie mają przypisanej sekcji i nie mają
roli administratora. Do czasu przypisania sekcji w panelu nie mogą pobierać ICS.
Migracja unieważnia stare sesje — trzeba się ponownie zalogować.

Każdy użytkownik, także administrator, widzi na stronie głównej tylko swoją
sekcję. Jego token działa wyłącznie dla tej sekcji; podmiana `section` w URL
zwraca HTTP 403. Po zmianie sekcji należy dodać nowy link ze strony głównej —
link ze starą sekcją przestaje działać. Uprawnienia są sprawdzane przy każdym
pobraniu kalendarza.

## Adres e-mail i powiadomienia

W menu **Powiadomienia** (`/account`) każdy zalogowany użytkownik może podać lub
zmienić swój adres e-mail i zaznaczyć checkbox powiadomień o zmianach planu.
Ustawienia dotyczą wyłącznie jego konta. Włączenie powiadomień wymaga poprawnego
adresu e-mail; przy wyłączonych powiadomieniach adres jest opcjonalny.
Administrator widzi adres i stan powiadomień na liście kont oraz może ustawić
te pola podczas tworzenia lub edycji użytkownika.

Po migracji `poetry run alembic upgrade head` istniejące konta mają pusty adres
i wyłączone powiadomienia. Nowe konta też mają je domyślnie wyłączone.
Skrypt obsługuje dodatkowe opcje:

```sh
poetry run python -m scripts.create_user jan --section 1 --email jan@example.com
```

Opcja `--email-notifications` włącza powiadomienia, jeśli użytkownik sobie ich
życzy. Bez niej sam włącza je po zalogowaniu.

Po wykryciu zmiany pliku planu endpoint `/api/schedules/files` pobiera odbiorców
z kont z włączonymi powiadomieniami i podanym adresem. `MAILS_TO` nie jest już
używane. Nadal obowiązuje globalne `IS_EMAILS_SEND=1` oraz konfiguracja SMTP.
Każdy adres otrzymuje osobny e-mail z załącznikiem; adresy innych odbiorców nie
są ujawniane. Powtórzone adresy dostają jedną wiadomość. Przy braku odbiorców,
wyłączonej wysyłce albo braku zmiany pliku odpowiedź `is_email_sent` wynosi `false`.
Wyłączenie powiadomień nie zmienia subskrypcji ICS.
