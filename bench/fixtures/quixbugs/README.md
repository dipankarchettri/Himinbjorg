# QuixBugs subset

From https://github.com/jkoppel/QuixBugs (MIT, see LICENSE), commit 4257f44b0ff1181dedaedee6a447e133219fcebf. Built by `bench/quixbugs_build.py`: each program's buggy
version, its JSON test cases, and its test rewritten to import the program
directly, plus a conftest.py that fails any test case running longer than
2 s (some buggy versions loop forever). Kept where the buggy version
fails and the correct one passes.

- bitcount
- bucketsort
- find_first_in_sorted
- find_in_sorted
- flatten
- gcd
- get_factors
- is_valid_parenthesization
- kheapsort
- kth
- lcs_length
- lis
- longest_common_subsequence
- max_sublist_sum
- mergesort
- next_palindrome
- next_permutation
- pascal
- possible_change
- powerset
- quicksort
- rpn_eval
- shunting_yard
- sieve
- sqrt
- subsequences
- to_base
- wrap
