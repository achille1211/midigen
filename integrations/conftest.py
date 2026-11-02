"""Lets `python -m pytest` from the repo root include the integrations tests.

They need the add-on package (`pip install -e integrations/python lupa`). Without it they are skipped instead of
breaking collection; optional pieces (fastapi/uvicorn, lupa, curl) are skipped inside the tests themselves.
"""
import importlib.util

collect_ignore_glob = ["ts-client/*", "examples/*"]

if importlib.util.find_spec("midigen_integrations") is None:  # pragma: no cover
    collect_ignore_glob += ["python/*", "reaper/*"]
    print("integrations: midigen_integrations not installed, skipping its tests "
          "(pip install -e integrations/python lupa)")
