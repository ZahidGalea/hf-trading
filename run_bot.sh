#!/usr/bin/env bash
# Lanza el ASMicroPriceMaker en Binance Futures Testnet.
#
# Antes de correr, sincronizar el reloj (especialmente en WSL2 tras suspensión):
#   sudo hwclock -s
#
# El script de arranque (live/run_testnet.py) verifica el desfase de reloj y aborta
# si supera ±500ms, mostrando instrucciones de sincronización.
set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Cargar credenciales desde .env si existe
if [ -f "$SCRIPT_DIR/.env" ]; then
    set -a
    # shellcheck source=/dev/null
    source "$SCRIPT_DIR/.env"
    set +a
fi

exec poetry run python -m live.run_testnet
