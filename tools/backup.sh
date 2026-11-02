#!/bin/sh
# Резервная копия базы и проверка, что она восстанавливается: sh tools/backup.sh
# Копия восстанавливается во временную базу, число строк сравнивается с рабочей базой.
set -eu

cd "$(dirname "$0")/.."
. ./.env

mkdir -p backups
file="backups/kb_$(date +%Y%m%d_%H%M%S).dump"
check=kb_restore_check

psql_db() {
    docker compose exec -T db psql -U "$POSTGRES_USER" -d "$1" -v ON_ERROR_STOP=1 -At -c "$2"
}

docker compose exec -T db pg_dump -U "$POSTGRES_USER" -Fc "$POSTGRES_DB" > "$file"
echo "Копия: $file, размер $(wc -c < "$file") байт"

psql_db postgres "DROP DATABASE IF EXISTS $check" > /dev/null
psql_db postgres "CREATE DATABASE $check" > /dev/null
docker compose exec -T db pg_restore -U "$POSTGRES_USER" -d "$check" --exit-on-error < "$file"

query="SELECT (SELECT count(*) FROM users) || ' ' || (SELECT count(*) FROM documents) || ' ' || (SELECT count(*) FROM document_versions)"
source_counts=$(psql_db "$POSTGRES_DB" "$query")
restored_counts=$(psql_db "$check" "$query")
psql_db postgres "DROP DATABASE $check" > /dev/null

echo "Пользователи, документы, версии в рабочей базе: $source_counts"
echo "Пользователи, документы, версии в копии:        $restored_counts"
if [ "$source_counts" != "$restored_counts" ]; then
    echo "Копия не совпадает с рабочей базой" >&2
    exit 1
fi
echo "Копия восстанавливается и совпадает с рабочей базой"
