import math
import random


def factorial(n):
    n = int(n)
    if n < 0 or n > 1000:
        raise ValueError("factorial input must be between 0 and 1000")
    return math.factorial(n)


def prime_check(n):
    n = int(n)
    if n > 10 ** 13:
        raise ValueError("number is too large for this demo")
    if n < 2:
        return False
    if n % 2 == 0:
        return n == 2
    i = 3
    while i * i <= n:
        if n % i == 0:
            return False
        i += 2
    return True


def sum_range(n):
    n = int(n)
    if n < 0 or n > 50_000_000:
        raise ValueError("sum_range input must be between 0 and 50,000,000")
    total = 0
    for i in range(1, n + 1):
        total += i
    return total


def sort_numbers(data):
    if isinstance(data, list):
        return sorted(data)
    count = int(data)
    if count < 1 or count > 2_000_000:
        raise ValueError("sort count must be between 1 and 2,000,000")
    numbers = [random.randint(0, 1_000_000) for _ in range(count)]
    numbers.sort()
    return {"count": count, "smallest": numbers[:5], "largest": numbers[-5:]}


def random_matrix(size):
    return [[random.randint(0, 9) for _ in range(size)] for _ in range(size)]


def matrix_multiply(data):
    if isinstance(data, dict):
        a, b = data["a"], data["b"]
    else:
        size = int(data)
        if size < 1 or size > 200:
            raise ValueError("matrix size must be between 1 and 200")
        a, b = random_matrix(size), random_matrix(size)
    if not a or not b or len(a[0]) != len(b):
        raise ValueError("matrix sizes do not match")
    columns = list(zip(*b))
    product = [[sum(x * y for x, y in zip(row, col)) for col in columns] for row in a]
    if isinstance(data, dict):
        return product
    return {"size": size, "trace": sum(product[i][i] for i in range(size)), "first_row": product[0][:3]}


OPERATIONS = {
    "factorial": factorial,
    "prime_check": prime_check,
    "sum_range": sum_range,
    "sort": sort_numbers,
    "matrix_multiply": matrix_multiply,
}
