PARAMETRIZE_NEGATIVE_TESTS = [
    ({"version": "4", "stream": "stable", "expected": "error"}),
    ({"version": "100.5.1", "stream": "stable", "expected": "error"}),
    ({"version": "100.5", "stream": "stable", "expected": "error"}),
    ({"version": "4.15.40", "stream": "stable", "expected": "error"}),
]
