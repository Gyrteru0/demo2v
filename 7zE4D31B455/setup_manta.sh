#!/bin/sh
# Готовый бинарь бэкенда без установки Go: скачивает backend/dota2-backend
# из GitHub Releases проекта и делает его исполняемым.
set -e
cd "$(dirname "$0")"
mkdir -p backend
URL="https://github.com/Gyrteru0/dem/releases/download/v1.0/dota2-backend"
echo "Скачиваю $URL"
if command -v curl >/dev/null 2>&1; then
  curl -L --fail -o backend/dota2-backend "$URL"
else
  wget -O backend/dota2-backend "$URL"
fi
chmod +x backend/dota2-backend
echo "Готово: backend/dota2-backend (Go не нужен)"
