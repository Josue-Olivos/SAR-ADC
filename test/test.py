# SPDX-FileCopyrightText: © 2024 Tiny Tapeout
# SPDX-License-Identifier: Apache-2.0

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge


# Tiny Tapeout output mapping:
#
# uo_out[3:0] = 4-bit SAR DAC result
# uo_out[4]   = sample switch
# uo_out[7:5] = FSM state

DAC_MASK = 0x0F

SAMPLE = 0
HOLD = 1
SET_BIT = 2
WAIT_DAC = 3
READ_COMP = 4
DONE = 5


def get_dac_code(dut):
    """Return the 4-bit DAC value from uo_out[3:0]."""
    return int(dut.uo_out.value) & DAC_MASK


def get_sample_switch(dut):
    """Return the sample-switch output from uo_out[4]."""
    return (int(dut.uo_out.value) >> 4) & 0x01


def get_state(dut):
    """Return the FSM state from uo_out[7:5]."""
    return (int(dut.uo_out.value) >> 5) & 0x07


async def reset_dut(dut):
    """Apply the active-low Tiny Tapeout reset."""

    dut._log.info("Reset")

    dut.ena.value = 1
    dut.ui_in.value = 0
    dut.uio_in.value = 0

    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 10)

    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 10)


async def comparator_model(dut, input_code):
    """
    Model the external analog comparator.

    input_code represents the analog input as an ideal 4-bit value
    from 0 to 15.

    Comparator HIGH means the trial DAC value should be kept.
    """

    while True:
        await RisingEdge(dut.clk)

        trial_code = get_dac_code(dut)

        if input_code >= trial_code:
            # ui_in[0] = comparator output
            dut.ui_in.value = 0x01
        else:
            dut.ui_in.value = 0x00


async def wait_for_done(dut, timeout_cycles=20_000):
    """Wait until the SAR state machine reaches the DONE state."""

    for _ in range(timeout_cycles):
        await RisingEdge(dut.clk)

        if get_state(dut) == DONE:
            return

    raise AssertionError(
        f"Timed out waiting for DONE state. "
        f"Current state = {get_state(dut)}, "
        f"DAC = {get_dac_code(dut):04b}"
    )


async def run_conversion(dut, input_code):
    """
    Run one complete ideal SAR conversion.

    Returns the final 4-bit DAC result.
    """

    dut._log.info(
        f"Testing simulated analog input code "
        f"{input_code:04b} ({input_code})"
    )

    comparator_task = cocotb.start_soon(
        comparator_model(dut, input_code)
    )

    await wait_for_done(dut)

    result = get_dac_code(dut)

    dut._log.info(
        f"Input code = {input_code:04b} ({input_code}), "
        f"SAR result = {result:04b} ({result})"
    )

    comparator_task.kill()

    return result


@cocotb.test()
async def test_project(dut):
    dut._log.info("Start 4-bit SAR ADC controller test")

    # The Verilog divider assumes a 50 MHz Tiny Tapeout clock.
    #
    # 20 ns period = 50 MHz
    clock = Clock(dut.clk, 20, unit="ns")
    cocotb.start_soon(clock.start())

    await reset_dut(dut)

    dut._log.info("Test reset behavior")

    # After reset, the controller should begin in SAMPLE.
    assert get_state(dut) == SAMPLE, (
        f"Expected SAMPLE state after reset, "
        f"but state was {get_state(dut)}"
    )

    assert get_sample_switch(dut) == 1, (
        "Sample switch should be enabled after reset"
    )

    assert get_dac_code(dut) == 0, (
        f"DAC should be 0000 after reset, "
        f"but was {get_dac_code(dut):04b}"
    )

    dut._log.info("Test SAR conversion for input code 10")

    result = await run_conversion(dut, 10)

    assert result == 10, (
        f"Expected SAR result 1010 (10), "
        f"but received {result:04b} ({result})"
    )


@cocotb.test()
async def test_low_input(dut):
    dut._log.info("Start low-input SAR test")

    clock = Clock(dut.clk, 20, unit="ns")
    cocotb.start_soon(clock.start())

    await reset_dut(dut)

    result = await run_conversion(dut, 3)

    assert result == 3, (
        f"Expected SAR result 0011 (3), "
        f"but received {result:04b} ({result})"
    )


@cocotb.test()
async def test_high_input(dut):
    dut._log.info("Start high-input SAR test")

    clock = Clock(dut.clk, 20, unit="ns")
    cocotb.start_soon(clock.start())

    await reset_dut(dut)

    result = await run_conversion(dut, 14)

    assert result == 14, (
        f"Expected SAR result 1110 (14), "
        f"but received {result:04b} ({result})"
    )


@cocotb.test()
async def test_all_input_codes(dut):
    """
    Test every possible 4-bit ADC input code from 0 through 15.
    """

    dut._log.info("Start complete 4-bit SAR sweep")

    clock = Clock(dut.clk, 20, unit="ns")
    cocotb.start_soon(clock.start())

    for input_code in range(16):
        await reset_dut(dut)

        result = await run_conversion(dut, input_code)

        assert result == input_code, (
            f"Input {input_code:04b}: "
            f"expected {input_code:04b}, "
            f"received {result:04b}"
        )
