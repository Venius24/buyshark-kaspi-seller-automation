"""Статистика для eval автоответчика: kappa, интервалы, парное сравнение прогонов.

Только стандартная библиотека: модуль импортируется в pytest и в CI без
numpy/scipy.
"""
from __future__ import annotations

import math
import random
from collections import Counter


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95 % интервал Уилсона для доли k/n (как в docs/AUTORESPONDER_EVAL.md)."""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def cohen_kappa(a: list, b: list) -> float | None:
    """Каппа Коэна для двух разметок одних и тех же объектов.
    None — каппа не определена (обе разметки из одного класса)."""
    if len(a) != len(b):
        raise ValueError("разметки разной длины")
    n = len(a)
    if n == 0:
        return None
    observed = sum(1 for x, y in zip(a, b) if x == y) / n
    ca, cb = Counter(a), Counter(b)
    expected = sum(ca[c] * cb[c] for c in set(ca) | set(cb)) / (n * n)
    if expected == 1:
        return None
    return (observed - expected) / (1 - expected)


def confusion(a: list, b: list, classes: list) -> dict:
    """{класс_a: {класс_b: n}} — строки: первая разметка (человек), столбцы: вторая (судья)."""
    m = {x: {y: 0 for y in classes} for x in classes}
    for x, y in zip(a, b):
        m.setdefault(x, {c: 0 for c in classes}).setdefault(y, 0)
        m[x][y] += 1
    return m


def mcnemar_exact(b: int, c: int) -> float:
    """Двусторонний точный тест Макнемара по числу расхождений b (было хорошо → стало плохо)
    и c (было плохо → стало хорошо). Для парного сравнения «до/после» на одних кейсах."""
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def paired_bootstrap_diff(before: list[float], after: list[float], iters: int = 20000, seed: int = 7) -> tuple[float, float, float]:
    """Разница средних after-before с 95 % перцентильным бутстрэп-интервалом
    (ресемплинг кейсов, пары сохраняются). Значения — доли успеха по кейсу (0..1)."""
    if len(before) != len(after) or not before:
        raise ValueError("нужны парные непустые списки")
    n = len(before)
    diffs = [y - x for x, y in zip(before, after)]
    point = sum(diffs) / n
    rnd = random.Random(seed)
    samples = sorted(sum(diffs[rnd.randrange(n)] for _ in range(n)) / n for _ in range(iters))
    return point, samples[int(0.025 * iters)], samples[int(0.975 * iters) - 1]
