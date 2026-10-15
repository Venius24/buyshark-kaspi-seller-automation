import asyncio
from copy import deepcopy

from evals.run import evaluate, load_cases, run_cases


def test_deterministic_evals():
    results = asyncio.run(run_cases())
    assert len(results) >= 12
    assert all(r.passed for r in results), '\n'.join(f'{r.name}: {r.detail}' for r in results if not r.passed)


def test_evals_detect_hallucinated_numbers_and_wrong_trajectory():
    case = deepcopy(next(c for c in load_cases() if c['id'] == 'empty_search'))
    case['scenario'][-1]['text'] = 'Revenue 958630914'
    result = asyncio.run(evaluate(case))
    assert not result.passed and 'numbers absent' in result.detail
    case['scenario'][-1]['text'] = 'Нет данных'
    case['expected_tools'] = ['get_dumping_status']
    result = asyncio.run(evaluate(case))
    assert not result.passed and 'tools expected' in result.detail
