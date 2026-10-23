from .cli import main

if __name__ == "__main__":  # guard: multiprocessing workers re-import this module
    main()
