"""``python -m dtk.worker`` - the worker container's entrypoint.

The api and worker images are the same image with different entrypoints
(docs/design/09-deployment.md), so this module exists to be that second one.
"""

from dtk.worker.main import main

if __name__ == "__main__":
    main()
