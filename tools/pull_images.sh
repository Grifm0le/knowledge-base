#!/bin/sh
# Скачивает базовые образы, когда Docker Hub отвечает с перебоями: sh tools/pull_images.sh
# Берёт официальные образы из зеркал библиотеки Docker и даёт им обычные имена.
# Уже скачанные образы пропускает.
set -u

pull() {
    name=$1
    if docker image inspect "$name" > /dev/null 2>&1; then
        echo "$name: уже есть"
        return 0
    fi
    for mirror in public.ecr.aws/docker/library mirror.gcr.io/library; do
        attempt=1
        while [ "$attempt" -le 3 ]; do
            if docker pull -q "$mirror/$name"; then
                docker tag "$mirror/$name" "$name"
                docker rmi "$mirror/$name" > /dev/null
                echo "$name: скачан из $mirror"
                return 0
            fi
            attempt=$((attempt + 1))
        done
    done
    if docker pull -q "$name"; then
        echo "$name: скачан из Docker Hub"
        return 0
    fi
    echo "$name: не удалось скачать" >&2
    return 1
}

status=0
for image in python:3.13-slim postgres:18 nginx:1.29-alpine redis:8; do
    pull "$image" || status=1
done
exit "$status"
