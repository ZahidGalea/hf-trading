from __future__ import annotations


def check_safety(metrics: dict, q_max: float) -> tuple[bool, str]:
    """Check emergency conditions. Returns (is_emergency, message).

    If is_emergency is True the bot must be stopped immediately.
    Conditions are checked in priority order; the first match is returned.
    """
    if metrics["cancel_rejected"] > 5:
        return True, (
            f"EMERGENCIA: {metrics['cancel_rejected']} cancel_rejected — "
            "reloj desincronizado. Sincronizar: sudo hwclock -s"
        )
    if metrics["submit_rate_per_s"] > 20:
        return True, (
            f"EMERGENCIA: submit flood — {metrics['submit_rate_per_s']:.1f} órdenes/s. "
            "Revisar throttle."
        )
    if metrics["inventory_max_abs"] > q_max * 1.5:
        return True, (
            f"EMERGENCIA: inventario excesivo — {metrics['inventory_max_abs']:.4f} BTC "
            f"(límite {q_max * 1.5:.4f}). Posible fill sin cancelación."
        )
    if metrics["reject_pct"] > 15:
        return True, (
            f"EMERGENCIA: tasa de rechazo crítica — {metrics['reject_pct']:.1f}%."
        )
    return False, ""
