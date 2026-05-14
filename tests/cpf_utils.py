"""Geração de CPF válido (11 dígitos) para testes — evita colisão na BD persistente."""

from __future__ import annotations

import random


def cpf_digitos_validos() -> str:
    """Retorna 11 dígitos que passam em ``validar_cpf`` (sem pontuação)."""
    while True:
        n = [random.randint(0, 9) for _ in range(9)]
        if len(set(n)) == 1:
            continue
        s = sum(n[i] * (10 - i) for i in range(9))
        d1 = (s * 10) % 11
        d1 = 0 if d1 >= 10 else d1
        n10 = n + [d1]
        s2 = sum(n10[i] * (11 - i) for i in range(10))
        d2 = (s2 * 10) % 11
        d2 = 0 if d2 >= 10 else d2
        return ''.join(str(x) for x in n + [d1, d2])
