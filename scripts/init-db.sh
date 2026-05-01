#!/bin/bash
# =============================================================================
# Tecno Táctil — Script de inicialización de bases de datos PostgreSQL
# Crea automáticamente múltiples bases de datos al iniciar el contenedor.
# Variable: POSTGRES_MULTIPLE_DATABASES = "db1,db2,db3"
# =============================================================================

set -e

# Función para crear una base de datos si no existe
create_database() {
    local database=$1
    echo "  → Creando base de datos: $database"
    psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" <<-EOSQL
        SELECT 'CREATE DATABASE $database'
        WHERE NOT EXISTS (
            SELECT FROM pg_database WHERE datname = '$database'
        )\gexec

        GRANT ALL PRIVILEGES ON DATABASE $database TO $POSTGRES_USER;
EOSQL
}

# Crear múltiples bases de datos si está definida la variable
if [ -n "$POSTGRES_MULTIPLE_DATABASES" ]; then
    echo "======================================"
    echo " Tecno Táctil — Inicializando bases de datos"
    echo "======================================"

    # Iterar sobre la lista separada por comas
    for db in $(echo "$POSTGRES_MULTIPLE_DATABASES" | tr ',' ' '); do
        create_database "$db"
    done

    echo "======================================"
    echo " ✓ Bases de datos inicializadas correctamente"
    echo "======================================"
fi
