import pandas as pd
import pytest

from signal_qaqc.io import read_table


@pytest.mark.parametrize("answer", ["None", "N/A", "NA", "null"])
def test_read_table_preserves_literal_answers_and_detects_empty_cells(tmp_path, answer):
    path = tmp_path / "signals.csv"
    path.write_text(f"signal_id,preemption\n1,{answer}\n2,\n", encoding="utf-8")

    table = read_table(path)

    assert table.loc[0, "preemption"] == answer
    assert pd.isna(table.loc[1, "preemption"])


@pytest.mark.parametrize("answer", ["None", "N/A", "NA", "null"])
def test_pandas_default_read_csv_treats_literal_answers_as_missing(tmp_path, answer):
    path = tmp_path / "signals.csv"
    path.write_text(f"signal_id,preemption\n1,{answer}\n2,\n", encoding="utf-8")

    assert pd.read_csv(path)["preemption"].isna().all()
