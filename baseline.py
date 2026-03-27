"""Compatibility wrapper for the package-local baseline entrypoint."""

from incident_commander.baseline import *  # noqa: F401,F403


if __name__ == "__main__":
    from incident_commander.baseline import main

    main()
