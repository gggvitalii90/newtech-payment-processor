from payment_processor.date_picker import month_grid, shift_month


def test_shift_month_crosses_year_boundaries() -> None:
    assert shift_month(2026, 1, -1) == (2025, 12)
    assert shift_month(2026, 12, 1) == (2027, 1)


def test_month_grid_is_monday_first_and_covers_all_days() -> None:
    weeks = month_grid(2026, 6)

    assert weeks[0] == [1, 2, 3, 4, 5, 6, 7]
    assert [day for week in weeks for day in week if day] == list(range(1, 31))