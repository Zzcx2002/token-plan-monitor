import pytest

from token_plan_monitor.window_chrome import resize_hit


@pytest.mark.parametrize("x,y,result", [(1, 1, 13), (150, 1, 12), (299, 1, 14),
    (1, 100, 10), (299, 100, 11), (1, 199, 16), (150, 199, 15), (299, 199, 17), (150, 100, 1)])
def test_all_native_resize_edges_and_client_content(x, y, result):
    assert resize_hit(x, y, 300, 200, 6) == result
