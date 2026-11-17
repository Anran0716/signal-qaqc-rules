"""Write fictional sample CSVs using the installed package."""

from pathlib import Path

from signal_qaqc.sample import main

if __name__ == "__main__":
    main(default_output=Path(__file__).resolve().parent)
