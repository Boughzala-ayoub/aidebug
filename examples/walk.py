import os
import sys


def add_one(value):
    answer = value + 1
    return answer


known = 41
nested = {"items": [3, 5, 8], "label": "test"}
result = add_one(known)
print(result, sys.argv[1:], os.getcwd())
