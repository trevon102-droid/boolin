from scans.projections import calibrate

CM = {"x": [0.1, 0.5, 0.9], "y": [0.2, 0.5, 0.7]}


def test_interpolates_and_clamps():
    assert calibrate(0.5, CM) == 0.5
    assert abs(calibrate(0.3, CM) - 0.35) < 1e-9
    assert calibrate(0.0, CM) == 0.2 and calibrate(1.0, CM) == 0.7


def test_monotone():
    ps = [i / 20 for i in range(21)]
    out = [calibrate(p, CM) for p in ps]
    assert out == sorted(out)
