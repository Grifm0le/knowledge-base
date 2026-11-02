#!/bin/sh
# Создаёт файл .env из образца и подставляет случайные секреты. Существующий файл не меняет.
set -eu

cd "$(dirname "$0")/.."

if [ -e .env ]; then
    echo ".env уже существует, оставляю без изменений"
    exit 0
fi

secret() {
    od -An -N32 -tx1 /dev/urandom | tr -d ' \n'
}

sed -e "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$(secret)/" \
    -e "s/^REDIS_PASSWORD=.*/REDIS_PASSWORD=$(secret)/" \
    -e "s/^KB_JWT_SECRET=.*/KB_JWT_SECRET=$(secret)/" \
    .env.example > .env
chmod 600 .env
echo "Создан .env со случайными секретами"
