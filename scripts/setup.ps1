# Установка с нуля на Windows. Запускать из корня репозитория.
$ErrorActionPreference = "Stop"

python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"
if ($LASTEXITCODE -ne 0) {
    Write-Host "Нужен Python 3.10 или новее."
    python -V
    exit 1
}

python -m venv .venv
& .\.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
pip install -e ".[telegram,cdp]"

Write-Host ""
Write-Host "Установлено."
Write-Host "1. Загрузите папку extension как распакованное расширение в браузере"
Write-Host "2. mb up"
Write-Host "3. mb status"
