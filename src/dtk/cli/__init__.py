"""The dtk command line.

The entry point is :data:`dtk.cli.main.app`, declared in ``pyproject.toml`` as
the ``dtk`` script. Submodules are imported directly rather than re-exported
here, so importing one command group does not drag in every other one - and so
the package can be imported without pulling in uvicorn or the HTTP stack.
"""
