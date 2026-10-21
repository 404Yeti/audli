#!/usr/bin/env bash
# [LOCAL] Starts or stops a disposable PostgreSQL 17 on 127.0.0.1 for the rehearsal and stores its URL (random password) in the
# private env file. It never contacts production. Engine: Docker if it is running, otherwise npm `embedded-postgres` (needs Node).
#   00-rehearsal-server.sh start|stop|status [--engine docker|npm] [--port N] [--env-file FILE]
. "$(dirname "$0")/lib.sh"
ACTION="${1:-}"; [ $# -eq 0 ] || shift
ENGINE=""; PORT=54329
while [ $# -gt 0 ]; do case "$1" in
  --engine) require_flag_value "$@"; ENGINE="$2"; shift 2;;
  --port) require_flag_value "$@"; PORT="$2"; shift 2;;
  --env-file) require_flag_value "$@"; ENV_FILE="$2"; shift 2;;
  *) die "unknown option $1";; esac; done
DIR="$ROOT/rehearsal-server"; NPM_VERSION=17.10.0-beta.17; NAME=audli-rehearsal
if [ -z "$ENGINE" ]; then if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then ENGINE=docker; else ENGINE=npm; fi; fi
npm_bin() { echo "$DIR/npm/node_modules/@embedded-postgres/linux-x64/native/bin"; }
case "$ACTION" in
  start)
    banner LOCAL "start a disposable rehearsal PostgreSQL 17 on 127.0.0.1:$PORT (engine: $ENGINE)"
    need python3; mkdir -p "$DIR"; chmod 700 "$ROOT" "$DIR"
    PASSWORD="$(python3 -c 'import secrets; print(secrets.token_hex(16))')"
    if [ "$ENGINE" = docker ]; then
      need docker; docker rm -f "$NAME" >/dev/null 2>&1 || true
      docker run -d --name "$NAME" -e POSTGRES_USER=audli_rehearsal -e POSTGRES_PASSWORD="$PASSWORD" -p "127.0.0.1:$PORT:5432" postgres:17 >/dev/null
      for _ in $(seq 60); do docker exec "$NAME" pg_isready -U audli_rehearsal >/dev/null 2>&1 && break; sleep 1; done
    elif [ "$ENGINE" = npm ]; then
      need node npm
      [ -d "$DIR/npm/node_modules/@embedded-postgres" ] || { mkdir -p "$DIR/npm"; ( cd "$DIR/npm" && npm init -y >/dev/null && npm install --no-audit --no-fund "embedded-postgres@$NPM_VERSION" >/dev/null ); }
      BIN="$(npm_bin)"; [ -x "$BIN/pg_ctl" ] || die "embedded PostgreSQL binaries not found for this platform (Linux x64 only); use --engine docker"
      [ ! -d "$DIR/data" ] || { "$BIN/pg_ctl" -D "$DIR/data" stop -m fast >/dev/null 2>&1 || true; rm -rf "$DIR/data"; }
      (umask 077; printf '%s' "$PASSWORD" > "$DIR/pwfile")
      "$BIN/initdb" -D "$DIR/data" -U audli_rehearsal -A scram-sha-256 --pwfile="$DIR/pwfile" --no-sync >/dev/null; rm -f "$DIR/pwfile"
      "$BIN/pg_ctl" -D "$DIR/data" -o "-p $PORT -c listen_addresses=127.0.0.1 -c unix_socket_directories=''" -l "$DIR/server.log" -w start >/dev/null
    else die "--engine must be docker or npm"; fi
    URL="postgresql://audli_rehearsal:$PASSWORD@127.0.0.1:$PORT/postgres"
    AUDLI_NEW_VALUE="$URL" env_file_set "$REHEARSAL_VAR"
    AUDLI_REHEARSAL_DATABASE_URL="$URL" python3 "$HERE/pgenv.py" "$REHEARSAL_VAR" --label | sed 's/^/  rehearsal: /'
    echo "STARTED. $REHEARSAL_VAR is stored in ${ENV_FILE:-$ENV_FILE_DEFAULT} (mode 600). Stop it later with: 00-rehearsal-server.sh stop --engine $ENGINE";;
  stop)
    banner LOCAL "stop the rehearsal PostgreSQL (engine: $ENGINE); its data is disposable"
    if [ "$ENGINE" = docker ]; then docker rm -f "$NAME" >/dev/null 2>&1 || true
    elif [ -d "$DIR/data" ]; then "$(npm_bin)/pg_ctl" -D "$DIR/data" stop -m fast >/dev/null 2>&1 || true; fi
    echo "STOPPED.";;
  status)
    if [ "$ENGINE" = docker ]; then docker ps --filter "name=$NAME" --format '  {{.Names}} {{.Status}}'
    elif [ -d "$DIR/data" ]; then "$(npm_bin)/pg_ctl" -D "$DIR/data" status | head -1 | sed 's/^/  /'; fi;;
  *) die "usage: 00-rehearsal-server.sh start|stop|status [--engine docker|npm] [--port N] [--env-file FILE]";;
esac
