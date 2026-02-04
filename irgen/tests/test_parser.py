import polars as pl

from irgen import parser


def test_parse_n_series_range() -> None:
    assert parser._parse_n_series("rega{n}, n=range(3)") == [0, 1, 2]
    assert parser._parse_n_series("rega{n}, n=range(1,4)") == [1, 2, 3]
    assert parser._parse_n_series("rega{n}, n=range(0,4,2)") == [0, 2]


def test_parse_n_series_tilde_and_count() -> None:
    assert parser._parse_n_series("rega{n}, n=0~2") == [0, 1, 2]
    assert parser._parse_n_series("rega{n}, n=3") == [0, 1, 2]


def test_parse_dataframe_expansion() -> None:
    df = pl.DataFrame(
        {
            "ADDR": ["0x10"],
            "REG": ["rega{n}, n=range(3)"],
            "FIELD": ["field0"],
            "BIT": ["[31:0]"],
            "WIDTH": [32],
            "ATTRIBUTE": ["RW"],
            "DEFAULT": ["0x0"],
            "DESCRIPTION": [""],
        }
    )

    parsed = parser.parse_dataframe(df)
    assert parsed["REG"].to_list() == ["rega_0", "rega_1", "rega_2"]
    assert parsed["ADDR"].to_list() == ["0x10", "0x14", "0x18"]
    assert parsed["stride"].unique().to_list() == [4]


def test_parse_default_int() -> None:
    assert parser._parse_default_int("0x10") == 16
    assert parser._parse_default_int("10") == 10
    assert parser._parse_default_int("null") is None
    assert parser._parse_default_int(None) is None
