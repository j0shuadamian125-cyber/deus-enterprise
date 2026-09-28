"""CLI operativa de HERMES: `python -m hermes.cli correo cli_001`."""
from __future__ import annotations

import argparse

from .config import construir_servicio
from .workers import bucle_correo, programar_seguimientos_vencidos


def main(argv: list[str] | None = None) -> int:
    analizador = argparse.ArgumentParser(prog="hermes", description=__doc__)
    subcomandos = analizador.add_subparsers(dest="comando", required=True)
    correo = subcomandos.add_parser("correo", help="sondea el buzon IMAP de un cliente")
    correo.add_argument("cliente_id")
    correo.add_argument("--segundos", type=int, default=60)
    correo.add_argument("--ciclos", type=int, default=None)
    seguimientos = subcomandos.add_parser(
        "seguimientos", help="programa seguimiento a los leads en silencio"
    )
    seguimientos.add_argument("cliente_id")
    seguimientos.add_argument("--horas", type=int, default=48)

    argumentos = analizador.parse_args(argv)
    servicio = construir_servicio()

    if argumentos.comando == "seguimientos":
        tocados = programar_seguimientos_vencidos(
            servicio, argumentos.cliente_id, argumentos.horas
        )
        print(f"seguimientos programados={len(tocados)}")
        return 0

    if not servicio.correo.recepcion_configurada:
        analizador.error("Faltan credenciales IMAP: IMAP_HOST, SMTP_USER, SMTP_PASSWORD")
    resumenes = bucle_correo(
        servicio, argumentos.cliente_id, argumentos.segundos, argumentos.ciclos
    )
    procesados = sum(r.procesados for r in resumenes)
    print(f"ciclos={len(resumenes)} procesados={procesados}")
    return 0


if __name__ == "__main__":  # pragma: no cover - entrada de proceso
    raise SystemExit(main())
