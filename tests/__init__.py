"""Marks the test tree as a package.

Without it `python3 -m unittest tests.test_reap` fails on import while
`unittest discover` works, so the two documented ways to run one module
disagree.
"""
