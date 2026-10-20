from collections import defaultdict
from fractions import Fraction
from itertools import combinations, product
import json
from math import ceil, exp, factorial, log, log2, sqrt
from pathlib import Path
from random import Random


ROOT = Path(__file__).resolve().parents[1]
NOISE = Fraction(1, 5)


def parity(value):
    return value.bit_count() % 2


def kernel(action, world, noise=NOISE):
    truth = parity(action & world)
    return tuple(1 - noise if value == truth else noise for value in (0, 1))


def affinity(first, second):
    return sum(sqrt(float(left * right)) for left, right in zip(first, second))


def divergence(first, second):
    return sum(float(left) * log(float(left / right)) for left, right in zip(first, second) if left)


def entropy(probabilities):
    return -sum(float(value) * log2(float(value)) for value in probabilities if value)


def close(first, second, tolerance=1e-10):
    assert abs(first - second) <= tolerance, (first, second)


def solve_linear(rows, values):
    size = len(values)
    augmented = [[Fraction(value) for value in row] + [Fraction(target)] for row, target in zip(rows, values)]
    for column in range(size):
        pivot = next((index for index in range(column, size) if augmented[index][column]), None)
        if pivot is None:
            return None
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        scale = augmented[column][column]
        augmented[column] = [value / scale for value in augmented[column]]
        for index in range(size):
            if index != column:
                scale = augmented[index][column]
                augmented[index] = [left - scale * right for left, right in zip(augmented[index], augmented[column])]
    return [row[-1] for row in augmented]


def cover_game(matrix):
    strategies = len(matrix)
    conflicts = len(matrix[0])
    constraints = []
    for index in range(strategies):
        constraints.append([int(position == index) for position in range(strategies)] + [0])
    constraints.extend([[matrix[index][conflict] for index in range(strategies)] + [-1] for conflict in range(conflicts)])
    best = None
    for active in combinations(constraints, strategies):
        solution = solve_linear([[1] * strategies + [0], *active], [1] + [0] * strategies)
        if solution is None or min(solution[:-1]) < 0:
            continue
        weights, value = solution[:-1], solution[-1]
        if any(sum(weights[index] * matrix[index][conflict] for index in range(strategies)) < value for conflict in range(conflicts)):
            continue
        if best is None or value > best[0]:
            best = value, weights
    assert best is not None
    return best


def check_coverage():
    checked = 0
    for dimension in range(1, 4):
        vectors = list(range(1, 2 ** dimension))
        for count in range(1, min(3, len(vectors)) + 1):
            for actions in combinations(vectors, count):
                span = {0}
                for action in actions:
                    span |= {value ^ action for value in tuple(span)}
                for target in vectors:
                    differences = [value for value in vectors if parity(value & target)]
                    matrix = [[parity(action & difference) for difference in differences] for action in actions]
                    value, weights = cover_game(matrix)
                    dual, _ = cover_game([[-matrix[index][conflict] for index in range(count)] for conflict in range(len(differences))])
                    assert value == -dual
                    assert (value > 0) == (target in span)
                    assert sum(weights) == 1
                    checked += 1
    calibration, _ = cover_game([[1, 0], [0, 1]])
    assert calibration == Fraction(1, 2)
    for size in range(1, 5):
        identity = [[int(first == second) for second in range(size)] for first in range(size)]
        value, _ = cover_game(identity)
        assert value == Fraction(1, size)
    return {'exact_primal_dual_and_span_instances': checked, 'calibration_mu': str(calibration), 'basis_sizes': 4}


def records_for_policy(world, policy, horizon):
    records = {(): Fraction(1)}
    for depth in range(horizon):
        updated = defaultdict(Fraction)
        for history, mass in records.items():
            action = policy(history)
            outcomes = (Fraction(1),) if action == 0 else kernel(action, world)
            for outcome, probability in enumerate(outcomes):
                updated[history + ((action, outcome),)] += mass * probability
        records = dict(updated)
    return records


def check_adaptive_ledger():
    checks = 0
    count_checks = 0
    policies = []
    for mask in range(128):
        def policy(history, mask=mask):
            position = 0
            for _, outcome in history:
                position = 2 * position + 1 + outcome
            return (3, 2)[(mask >> position) & 1]
        policies.append(policy)
    policies.append(lambda history: 0 if history and (history[0][1] == 1 or history[-1][0] == 0) else 3)
    for policy in policies:
        distributions = [records_for_policy(world, policy, 3) for world in range(4)]
        grouped = defaultdict(lambda: [Fraction(0)] * 4)
        ratios = {}
        for history in distributions[0]:
            counts = tuple(sum(operation == action and value == outcome for operation, value in history) for action in (2, 3) for outcome in (0, 1))
            ratio = tuple(distributions[world][history] / distributions[0][history] for world in range(4))
            assert counts not in ratios or ratios[counts] == ratio
            ratios[counts] = ratio
            for world in range(4):
                grouped[counts][world] += distributions[world][history]
            count_checks += 1
        for counts, masses in grouped.items():
            assert tuple(mass / masses[0] for mass in masses) == ratios[counts]
        for first in range(4):
            expected_counts = {action: sum(mass * sum(operation == action for operation, _ in history) for history, mass in distributions[first].items()) for action in (2, 3)}
            for second in range(4):
                if first == second:
                    continue
                full = sum(float(mass) * log(float(mass / distributions[second][history])) for history, mass in distributions[first].items())
                ledger = sum(float(expected_counts[action]) * divergence(kernel(action, first), kernel(action, second)) for action in (2, 3))
                close(full, ledger)
                checks += 1
    blind = [records_for_policy(world, lambda history: 3, 5) for world in range(4)]
    assert blind[0] == blind[3] and blind[1] == blind[2]
    risk = sum(sum(distribution.get(history, 0) for distribution in blind) - max(sum(blind[world].get(history, 0) for world in range(4) if world % 2 == target) for target in (0, 1)) for history in blind[0]) / 4
    assert risk == Fraction(1, 2)
    return {'policies': len(policies), 'kl_chain_equalities': checks, 'adaptive_count_checks': count_checks, 'persistent_bias_risk': str(risk)}


def check_dimension_and_inequality():
    noise_entropy = entropy((NOISE, 1 - NOISE))
    expected_cover = (1 - 2 * sqrt(float(NOISE * (1 - NOISE)))) / 2
    pairs = 0
    dimensions = []
    for dimension in range(1, 5):
        size = 2 ** dimension
        distributions = [tuple(probability / size for action in range(size) for probability in kernel(action, world)) for world in range(size)]
        for first, second in combinations(range(size), 2):
            close(1 - affinity(distributions[first], distributions[second]), expected_cover)
            pairs += 1
        average = [sum(distributions[world][index] for world in range(size)) / size for index in range(2 * size)]
        information = entropy(average) - sum(entropy(distribution) for distribution in distributions) / size
        close(information, (1 - 1 / size) * (1 - noise_entropy))
        dimensions.append({'hidden_bits': dimension, 'coverage': expected_cover, 'one_call_information_bits': information})
    generator = Random(20260913)
    for _ in range(200):
        raw_first = [generator.randint(1, 30) for _ in range(4)]
        raw_second = [generator.randint(1, 30) for _ in range(4)]
        first = [Fraction(value, sum(raw_first)) for value in raw_first]
        second = [Fraction(value, sum(raw_second)) for value in raw_second]
        ratio = max(float(left / right) for left, right in zip(first, second))
        constant = 2 * (sqrt(ratio) + 1) ** 2
        assert divergence(first, second) <= constant * (1 - affinity(first, second)) + 1e-10
    return {'dimension_pair_checks': pairs, 'dimension_examples': dimensions, 'kl_hellinger_comparisons': 200}


def partitions(size):
    if size == 0:
        yield ()
        return
    for prefix in partitions(size - 1):
        for label in range(max(prefix, default=-1) + 2):
            yield prefix + (label,)


def collapse(distribution, grouping):
    return tuple(sum(value for value, label in zip(distribution, grouping) if label == selected) for selected in range(max(grouping) + 1))


def check_communication():
    generator = Random(7142026)
    comparisons = 0
    for _ in range(40):
        raw = [[generator.randint(1, 20) for _ in range(4)] for _ in range(2)]
        first, second = [[Fraction(value, sum(row)) for value in row] for row in raw]
        for grouping in partitions(4):
            reduced_first, reduced_second = collapse(first, grouping), collapse(second, grouping)
            assert affinity(reduced_first, reduced_second) + 1e-12 >= affinity(first, second)
            comparisons += 1
    first = [Fraction(value, 8) for value in (1, 1, 3, 3)]
    second = list(reversed(first))
    close(affinity(first, second), affinity(collapse(first, (0, 0, 1, 1)), collapse(second, (0, 0, 1, 1))))
    grouped = defaultdict(lambda: [Fraction(0)] * 4)
    ratios = {}
    checked = 0
    for sequence in product(range(4), repeat=4):
        counts = tuple(sequence.count(symbol) for symbol in range(4))
        probabilities = []
        for world in range(4):
            probability = Fraction(1)
            for symbol in sequence:
                action, outcome = (3, 2)[symbol // 2], symbol % 2
                probability *= kernel(action, world)[outcome] / 2
            probabilities.append(probability)
            grouped[counts][world] += probability
        ratio = tuple(probability / probabilities[0] for probability in probabilities)
        assert counts not in ratios or ratios[counts] == ratio
        ratios[counts] = ratio
        checked += 1
    for counts, probabilities in grouped.items():
        assert tuple(probability / probabilities[0] for probability in probabilities) == ratios[counts]
    return {'garbling_checks': comparisons, 'lossless_partition': 'passed', 'raw_records': checked, 'sufficient_count_messages': len(grouped)}


def compositions(total, size):
    if size == 1:
        yield (total,)
    else:
        for first in range(total + 1):
            for remaining in compositions(total - first, size - 1):
                yield (first,) + remaining


def check_decoder_risk():
    coverage = (1 - 2 * sqrt(float(NOISE * (1 - NOISE)))) / 2
    estimated_noise = Fraction(21, 100)
    epsilon = max(abs(log(float(estimated_noise / NOISE))), abs(log(float((1 - estimated_noise) / (1 - NOISE)))))
    results = []
    for count in (4, 10, 20, 40):
        risks = [0.0] * 4
        for counts in compositions(count, 4):
            coefficient = factorial(count)
            for amount in counts:
                coefficient //= factorial(amount)
            scores = [sum(amount * log(float(kernel((3, 2)[symbol // 2], world, estimated_noise)[symbol % 2])) for symbol, amount in enumerate(counts)) for world in range(4)]
            selected = max(range(4), key=lambda world: scores[world])
            for world in range(4):
                if selected % 2 == world % 2:
                    continue
                probability = float(coefficient)
                for symbol, amount in enumerate(counts):
                    probability *= (float(kernel((3, 2)[symbol // 2], world)[symbol % 2]) / 2) ** amount
                risks[world] += probability
        bound = min(1, 2 * exp(count * epsilon) * (1 - coverage) ** count)
        assert max(risks) <= bound + 1e-10
        results.append({'calls': count, 'worst_mismatched_decoder_risk': max(risks), 'proved_upper_bound': bound})
    return results


def check_sequential_stopping():
    delta = Fraction(1, 20)
    results = []
    for truth in (0, 1):
        live = {(0, 0): Fraction(1)}
        error = Fraction(0)
        for horizon in range(1, 61):
            updated = defaultdict(Fraction)
            for (zeros, ones), mass in live.items():
                for outcome, probability in enumerate(kernel(1, truth)):
                    next_zeros, next_ones = zeros + int(outcome == 0), ones + int(outcome == 1)
                    odds = Fraction(4) ** (next_ones - next_zeros)
                    selected = 1 if odds >= 1 / delta else 0 if odds <= delta else None
                    if selected is None:
                        updated[next_zeros, next_ones] += mass * probability
                    elif selected != truth:
                        error += mass * probability
            live = dict(updated)
            tail = float(sum(live.values()))
            assert tail <= min(1, sqrt(float(1 / delta)) * 0.8 ** horizon) + 1e-11
            assert error <= delta
        results.append({'truth': truth, 'horizon': 60, 'wrong_stopping_probability': float(error), 'remaining_probability': float(sum(live.values()))})
    return results


def write_table():
    rows = []
    delta = 0.05
    binary_kl = (1 - 2 * delta) * log((1 - delta) / delta)
    for noise in (0.1, 0.2, 0.3, 0.4):
        coverage = (1 - 2 * sqrt(noise * (1 - noise))) / 2
        local_kl = (1 - 2 * noise) * log((1 - noise) / noise) / 2
        rows.append((noise, coverage, ceil(binary_kl / local_kl), ceil(log(2 / delta) / coverage)))
    (ROOT / 'data/calibration.csv').write_text('noise,coverage,necessary_calls,sufficient_calls\n' + ''.join(f'{noise},{coverage:.12g},{lower},{upper}\n' for noise, coverage, lower, upper in rows))
    table = '\\begin{tabular}{rrrr}\n\\toprule\n噪声 $\\eta$ & 覆盖率 & 必要调用下界 & 充分调用预算\\\\\n\\midrule\n'
    table += ''.join(f'{noise:.1f} & {coverage:.4f} & {lower} & {upper}\\\\\n' for noise, coverage, lower, upper in rows)
    table += '\\bottomrule\n\\end{tabular}\n'
    (ROOT / 'data/calibration-table.tex').write_text(table)
    return rows


def main():
    report = {
        'edition': 3,
        'status': 'passed',
        'coverage_and_span': check_coverage(),
        'adaptive_ledger': check_adaptive_ledger(),
        'dimension_and_comparison': check_dimension_and_inequality(),
        'communication': check_communication(),
        'robust_decoder': check_decoder_risk(),
        'sequential_stopping': check_sequential_stopping(),
        'calibration_table': write_table(),
    }
    serialized = json.dumps(report, ensure_ascii=False, indent=2)
    (ROOT / 'validation/theory-check.json').write_text(serialized + '\n')
    print(serialized)


if __name__ == '__main__':
    main()
