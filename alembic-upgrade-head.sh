#!/bin/bash

# Ścieżka do pliku ze zmiennymi środowiskowymi
ENV_FILE="/home/michal/workspaces/studify/environment/local/backend/backend.env"

# Załaduj zmienne środowiskowe
# Załaduj zmienne środowiskowe
if [ -f "$ENV_FILE" ]; then
  # Wczytaj tylko niepuste linie, bez komentarzy
  export $(grep -v '^#' "$ENV_FILE" | xargs)

  echo "Zaimportowane zmienne środowiskowe:"
  grep -v '^#' "$ENV_FILE" | while IFS= read -r line; do
    if [ -n "$line" ]; then
      var_name=$(echo "$line" | cut -d '=' -f1)
      echo "$var_name=${!var_name}"
    fi
  done
else
  echo "Plik $ENV_FILE nie istnieje!"
  exit 1
fi

# Uruchom Alembic
alembic upgrade head
