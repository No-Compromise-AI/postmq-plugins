"""Pure allocation-observation controls; no GPU, process, network or file IO."""
def require(ok, message):
    if not ok:
        raise RuntimeError(message)

def positive_control(before, during):
    require(before["total_bytes"] == during["total_bytes"], "allocation total differs")
    delta = before["free_bytes"] - during["free_bytes"]
    require(delta >= 96 * 1024 ** 2,
            "inconclusive: CUDA observer did not see at least 96 MiB of the 128 MiB allocation")
    require(delta <= 160 * 1024 ** 2, "inconclusive: allocation observation contaminated by excess drop")
