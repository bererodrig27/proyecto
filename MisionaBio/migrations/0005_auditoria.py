"""
Triggers de auditoría (Oracle / PL/SQL).

Crea un trigger AFTER INSERT OR UPDATE OR DELETE ... FOR EACH ROW sobre las tablas
críticas: registro, especie, usuario y extraccion_ia. Cada cambio inserta una fila
en la tabla "auditoria" (creada en 0001_initial) con:

    tabla_afectada, id_registro_afectado, operacion (INSERT/UPDATE/DELETE),
    valores_anteriores (JSON, :OLD), valores_nuevos (JSON, :NEW),
    usuario_bd (usuario de conexión de la base) y fecha_hora (UTC).

Notas de diseño:
  * En un DELETE se guarda el estado completo de la fila en valores_anteriores
    y valores_nuevos queda en NULL, así se puede reconstruir lo eliminado.
  * En un UPDATE, si el JSON anterior y el nuevo son idénticos (por ejemplo, un
    save() que no cambió nada) no se genera registro.
  * usuario: nunca se guarda el hash de la contraseña. Se guarda una huella SHA-256
    (password_sha256) solo para poder detectar que la contraseña cambió.
    last_login no se audita (cambia en cada login y solo genera ruido).
  * fecha_hora se guarda en UTC, igual que el resto de los DateTimeField de Django
    con USE_TZ = True sobre Oracle.
  * Solo se ejecuta en Oracle (PL/SQL). En otros motores (SQLite de los tests) no hace nada.
  * El usuario que ejecute esta migración necesita el privilegio CREATE TRIGGER
    (el usuario "django" de la imagen gvenzl/oracle-xe lo tiene vía el rol RESOURCE).
"""

from django.db import migrations


# Tipos de columna para armar el JSON:
#   (sin tipo) -> columna simple (VARCHAR2, NUMBER, DATE, TIMESTAMP)
#   "lob"      -> NCLOB de texto (TextField)
#   "json"     -> NCLOB que ya contiene JSON (JSONField): se incrusta como JSON, no como string
#   "hash"     -> se guarda solo la huella SHA-256 (para no registrar contraseñas)
TABLAS = {
    "usuario": {
        "pk": "id",
        "cols": [
            ("username", None), ("first_name", None), ("last_name", None),
            ("email", None), ("is_active", None), ("is_staff", None),
            ("is_superuser", None), ("id_rol", None), ("date_joined", None),
            ("fecha_alta", None), ("password", "hash"),
        ],
    },
    "especie": {
        "pk": "id_especie",
        "cols": [
            ("nombre_cientifico", None), ("nombre_comun", None),
            ("clase_taxonomica", None), ("taxonomia", None),
            ("estado_conservacion", None), ("descripcion", "lob"),
        ],
    },
    "registro": {
        "pk": "id_registro",
        "cols": [
            ("id_noticia", None), ("origen_registro", None), ("id_especie", None),
            ("id_ubicacion", None), ("fecha_avistamiento", None),
            ("tipo_registro", None), ("estado_calidad", None),
            ("fragmento_origen", "lob"), ("observaciones", "lob"),
            ("id_usuario_carga", None), ("id_usuario_validador", None),
            ("fecha_validacion", None),
        ],
    },
    "extraccion_ia": {
        "pk": "id_extraccion",
        "cols": [
            ("id_registro", None), ("modelo", None), ("version_prompt", None),
            ("json_extraido", "json"), ("confianza", None),
            ("estado_revision", None), ("json_corregido", "json"),
            ("id_usuario_revisor", None), ("fecha_extraccion", None),
            ("fecha_revision", None),
        ],
    },
}


def _nombre_trigger(tabla):
    return f"trg_aud_{tabla}"


def _json_objeto(prefijo, cols):
    """Arma el JSON_OBJECT(...) con las columnas de :OLD o :NEW."""
    partes = []
    for col, tipo in cols:
        ref = f":{prefijo}.{col}"
        if tipo == "lob":
            valor = f"TO_CLOB({ref})"
        elif tipo == "json":
            valor = f"TO_CLOB({ref}) FORMAT JSON"
        elif tipo == "hash":
            col = f"{col}_sha256"
            valor = f"RAWTOHEX(STANDARD_HASH({ref}, 'SHA256'))"
        else:
            valor = ref
        partes.append(f"                '{col}' VALUE {valor}")
    return "JSON_OBJECT(\n" + ",\n".join(partes) + "\n                RETURNING CLOB)"


def _sql_trigger(tabla, pk, cols):
    json_old = _json_objeto("OLD", cols)
    json_new = _json_objeto("NEW", cols)
    # IMPORTANTE: el script termina en "END;" + salto de línea. Django le quita al
    # final un ";" o "/" antes de enviarlo a Oracle; con el salto de línea el ";"
    # de END; se conserva.
    return f"""CREATE OR REPLACE TRIGGER {_nombre_trigger(tabla)}
AFTER INSERT OR UPDATE OR DELETE ON {tabla}
FOR EACH ROW
DECLARE
    v_operacion VARCHAR2(10);
    v_id        NUMBER;
    v_anterior  CLOB;
    v_nuevo     CLOB;
BEGIN
    IF INSERTING THEN
        v_operacion := 'INSERT';
        v_id := :NEW.{pk};
        SELECT {json_new}
          INTO v_nuevo FROM dual;

    ELSIF UPDATING THEN
        v_operacion := 'UPDATE';
        v_id := :NEW.{pk};
        SELECT {json_old}
          INTO v_anterior FROM dual;
        SELECT {json_new}
          INTO v_nuevo FROM dual;
        -- Si no cambió ningún campo auditado, no se registra nada.
        IF DBMS_LOB.COMPARE(v_anterior, v_nuevo) = 0 THEN
            RETURN;
        END IF;

    ELSE
        v_operacion := 'DELETE';
        v_id := :OLD.{pk};
        SELECT {json_old}
          INTO v_anterior FROM dual;
    END IF;

    INSERT INTO auditoria (
        tabla_afectada, id_registro_afectado, operacion,
        valores_anteriores, valores_nuevos, usuario_bd, fecha_hora
    ) VALUES (
        '{tabla}', v_id, v_operacion,
        v_anterior, v_nuevo,
        SYS_CONTEXT('USERENV', 'SESSION_USER'),
        SYS_EXTRACT_UTC(SYSTIMESTAMP)
    );
END;
"""


SQL_CREAR = [_sql_trigger(t, d["pk"], d["cols"]) for t, d in TABLAS.items()]
SQL_BORRAR = [f"DROP TRIGGER {_nombre_trigger(t)}" for t in TABLAS]


def crear_triggers(apps, schema_editor):
    """Los triggers son PL/SQL: solo se crean en Oracle (en otros motores, p. ej. SQLite
    para los tests, no se hace nada)."""
    if schema_editor.connection.vendor != "oracle":
        return
    for sql in SQL_CREAR:
        schema_editor.execute(sql, params=None)


def borrar_triggers(apps, schema_editor):
    if schema_editor.connection.vendor != "oracle":
        return
    for sql in SQL_BORRAR:
        schema_editor.execute(sql, params=None)


class Migration(migrations.Migration):

    dependencies = [
        ('MisionaBio', '0004_registro_observaciones_registro_origen_registro_and_more'),
    ]

    operations = [
        migrations.RunPython(crear_triggers, borrar_triggers),
    ]