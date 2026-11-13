#!/usr/bin/env bash
# Установка с нуля. Запускать из корня репозитория.
set -e

PY=${PYTHON:-python3}

if ! $PY -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'; then
    echo "Нужен Python 3.10 или новее. Текущий: $($PY -V 2>&1)"
    exit 1
fi

$PY -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate

python -m pip install --upgrade pip
pip install -e ".[telegram,cdp]"

echo
echo "Установлено."
echo "1. Загрузите папку extension как распакованное расширение в браузере"
echo "2. mb up"
echo "3. mb status"
