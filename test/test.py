# SPDX-FileCopyrightText: © 2024 Tiny Tapeout
# SPDX-License-Identifier: Apache-2.0

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, ReadOnly, RisingEdge


# Tiny Tapeout output mapping:
#
# ui_in[0]    = comparator output
# uio_in[0]   = Trojan enable
#
# uo_out[3:0] = physical DAC switch controls
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
    """Return the physical 4-bit DAC output from uo_out[3:0]."""
    return int(dut.uo_out.value) & DAC_MASK


def get_sample_switch(dut):
    """Return the sample-switch output from uo_out[4]."""
    return (int(dut.uo_out.value) >> 4) & 0x01


def get_state(dut):
    """Return the FSM state from uo_out[7:5]."""
    return (int(dut.uo_out.value) >> 5) & 0x07


def set_trojan_enable(dut, enabled):
    """
    Set uio_in[0], the experimental Trojan-enable input.

    enabled = False:
        uio_in[0] = 0

    enabled = True:
        uio_in[0] = 1
    """

    dut.uio_in.value = 0x01 if enabled else 0x00


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
    Behavioral model of the external analog comparator and DAC.

    input_code represents an ideal analog input from 0 through 15.

    The comparator observes the physical DAC output on uo_out[3:0].
    This is important because the Trojan inverts those physical
    outputs during its active phase.
    """

    while True:
        await RisingEdge(dut.clk)

        trial_code = get_dac_code(dut)

        if input_code >= trial_code:
            # Comparator HIGH: keep the current SAR trial bit.
            dut.ui_in.value = 0x01
        else:
            # Comparator LOW: clear the current SAR trial bit.
            dut.ui_in.value = 0x00


async def wait_for_conversion_done(dut, timeout_cycles=20_000):
    """
    Wait for the next complete conversion.

    This function first ensures that the previous DONE state has
    been exited, then waits for the next DONE state.
    """

    # Leave the previous DONE state, when necessary.
    for _ in range(timeout_cycles):
        await RisingEdge(dut.clk)
        await ReadOnly()

        if get_state(dut) != DONE:
            break
    else:
        raise AssertionError("Controller remained stuck in DONE")

    # Wait for the next conversion to reach DONE.
    for _ in range(timeout_cycles):
        await RisingEdge(dut.clk)
        await ReadOnly()

        if get_state(dut) == DONE:
            return get_dac_code(dut)

    raise AssertionError(
        f"Timed out waiting for DONE. "
        f"State={get_state(dut)}, "
        f"DAC={get_dac_code(dut):04b}"
    )


async def run_conversion(dut, input_code):
    """Wait for one conversion and return its physical DAC result."""

    result = await wait_for_conversion_done(dut)

    dut._log.info(
        f"Input={input_code:04b} ({input_code}), "
        f"output={result:04b} ({result})"
    )

    return result


async def start_test(dut):
    """Start the 50 MHz clock and reset the design."""

    clock = Clock(dut.clk, 20, unit="ns")
    cocotb.start_soon(clock.start())

    await reset_dut(dut)


#================================================================
# CLEAN-DESIGN TESTS
#================================================================

@cocotb.test()
async def test_project(dut):
    """Test reset and one normal conversion."""

    dut._log.info("Start basic SAR ADC test")

    await start_test(dut)

    assert get_state(dut) == SAMPLE, (
        f"Expected SAMPLE after reset, got state {get_state(dut)}"
    )

    assert get_sample_switch(dut) == 1, (
        "Sample switch should be enabled after reset"
    )

    assert get_dac_code(dut) == 0, (
        f"DAC should be 0000 after reset, "
        f"got {get_dac_code(dut):04b}"
    )

    set_trojan_enable(dut, False)

    input_code = 10

    comparator_task = cocotb.start_soon(
        comparator_model(dut, input_code)
    )

    result = await run_conversion(dut, input_code)

    assert result == input_code, (
        f"Expected {input_code:04b}, got {result:04b}"
    )

    comparator_task.kill()


@cocotb.test()
async def test_low_input(dut):
    """Test a low input with the Trojan disabled."""

    dut._log.info("Start low-input clean test")

    await start_test(dut)
    set_trojan_enable(dut, False)

    input_code = 3

    comparator_task = cocotb.start_soon(
        comparator_model(dut, input_code)
    )

    result = await run_conversion(dut, input_code)

    assert result == input_code, (
        f"Expected {input_code:04b}, got {result:04b}"
    )

    comparator_task.kill()


@cocotb.test()
async def test_high_input(dut):
    """Test a high input with the Trojan disabled."""

    dut._log.info("Start high-input clean test")

    await start_test(dut)
    set_trojan_enable(dut, False)

    input_code = 14

    comparator_task = cocotb.start_soon(
        comparator_model(dut, input_code)
    )

    result = await run_conversion(dut, input_code)

    assert result == input_code, (
        f"Expected {input_code:04b}, got {result:04b}"
    )

    comparator_task.kill()


@cocotb.test()
async def test_all_input_codes(dut):
    """Test every 4-bit input code with the Trojan disabled."""

    dut._log.info("Start complete clean 4-bit SAR sweep")

    await start_test(dut)
    set_trojan_enable(dut, False)

    for input_code in range(16):
        await reset_dut(dut)
        set_trojan_enable(dut, False)

        comparator_task = cocotb.start_soon(
            comparator_model(dut, input_code)
        )

        result = await run_conversion(dut, input_code)

        assert result == input_code, (
            f"Input {input_code:04b}: "
            f"expected {input_code:04b}, "
            f"got {result:04b}"
        )

        comparator_task.kill()


#================================================================
# TROJAN TESTS
#================================================================

@cocotb.test()
async def test_trojan_disabled_stays_clean(dut):
    """
    Verify that the phase counter cannot affect the DAC outputs
    while uio_in[0] is low.
    """

    dut._log.info("Test Trojan-disabled operation")

    await start_test(dut)
    set_trojan_enable(dut, False)

    input_code = 10

    comparator_task = cocotb.start_soon(
        comparator_model(dut, input_code)
    )

    # Run more than 500 conversions. No inversion should occur
    # because the Trojan-enable input is low.
    for conversion_number in range(1, 502):
        result = await run_conversion(dut, input_code)

        assert result == input_code, (
            f"Trojan was disabled, but conversion "
            f"{conversion_number} produced {result:04b}; "
            f"expected {input_code:04b}"
        )

    comparator_task.kill()


@cocotb.test()
async def test_trojan_phase_switching(dut):
    """
    Verify the alternating Trojan behavior:

        Conversions 1-499:
            normal final output

        Conversion 500:
            phase changes to inverted at the DONE edge

        Conversions 501-999:
            Trojan phase remains active

        Conversion 1000:
            phase changes back to normal at the DONE edge

        Conversion 1001:
            fully normal conversion again
    """

    dut._log.info("Start 500-normal / 500-inverted Trojan test")

    await start_test(dut)

    input_code = 10
    normal_code = input_code
    inverted_code = (~input_code) & DAC_MASK

    comparator_task = cocotb.start_soon(
        comparator_model(dut, input_code)
    )

    # Enable the phase counter.
    set_trojan_enable(dut, True)

    #------------------------------------------------------------
    # First 499 conversions should be completely normal.
    #------------------------------------------------------------

    for conversion_number in range(1, 500):
        result = await run_conversion(dut, input_code)

        assert result == normal_code, (
            f"Conversion {conversion_number} should be normal. "
            f"Expected {normal_code:04b}, got {result:04b}"
        )

    dut._log.info("First 499 conversions were normal")

    #------------------------------------------------------------
    # At the DONE edge of conversion 500, trojan_phase changes
    # from 0 to 1. The held output code becomes inverted.
    #------------------------------------------------------------

    result_500 = await run_conversion(dut, input_code)

    dut._log.info(
        f"Conversion 500 output: {result_500:04b}; "
        f"expected phase-switch value {inverted_code:04b}"
    )

    assert result_500 == inverted_code, (
        f"Conversion 500 should switch to inverted output. "
        f"Expected {inverted_code:04b}, got {result_500:04b}"
    )

    #------------------------------------------------------------
    # The next conversion runs entirely while physical DAC outputs
    # are inverted. Its result should no longer match the clean ADC.
    #------------------------------------------------------------

    result_501 = await run_conversion(dut, input_code)

    dut._log.info(
        f"First fully infected conversion output: "
        f"{result_501:04b}"
    )

    assert result_501 != normal_code, (
        "Trojan phase was active, but conversion 501 still "
        "matched the clean result"
    )

    #------------------------------------------------------------
    # Advance through conversions 502-999.
    #------------------------------------------------------------

    for conversion_number in range(502, 1000):
        result = await run_conversion(dut, input_code)

        assert result != normal_code, (
            f"Conversion {conversion_number} occurred during the "
            f"infected phase but produced the clean code "
            f"{normal_code:04b}"
        )

    dut._log.info("Completed the infected 500-conversion phase")

    #------------------------------------------------------------
    # Conversion 1000 toggles the phase back to normal at DONE.
    # This is a boundary conversion, so its internal SAR result was
    # still calculated while the inverted output phase was active.
    #------------------------------------------------------------

    result_1000 = await run_conversion(dut, input_code)

    dut._log.info(
        f"Conversion 1000 phase-boundary output: "
        f"{result_1000:04b}"
    )

    #------------------------------------------------------------
    # Conversion 1001 runs completely in the restored clean phase.
    # It must produce the correct result again.
    #------------------------------------------------------------

    result_1001 = await run_conversion(dut, input_code)

    dut._log.info(
        f"First restored clean conversion output: "
        f"{result_1001:04b}"
    )

    assert result_1001 == normal_code, (
        f"Expected normal operation to return on conversion 1001. "
        f"Expected {normal_code:04b}, got {result_1001:04b}"
    )

    comparator_task.kill()


@cocotb.test()
async def test_disabling_trojan_resets_sequence(dut):
    """
    Verify that pulling uio_in[0] low disables the Trojan,
    clears its phase, and resets the conversion counter.
    """

    dut._log.info("Test external Trojan disable")

    await start_test(dut)

    input_code = 10

    comparator_task = cocotb.start_soon(
        comparator_model(dut, input_code)
    )

    set_trojan_enable(dut, True)

    # Enter the infected phase.
    for _ in range(501):
        result = await run_conversion(dut, input_code)

    assert result != input_code, (
        "Expected the controller to be in the infected phase"
    )

    # Disable the Trojan through uio_in[0].
    set_trojan_enable(dut, False)

    # The next DONE state resets the counter and phase.
    await run_conversion(dut, input_code)

    # The following conversion runs entirely with normal outputs.
    restored_result = await run_conversion(dut, input_code)

    assert restored_result == input_code, (
        f"Disabling the Trojan did not restore normal operation. "
        f"Expected {input_code:04b}, "
        f"got {restored_result:04b}"
    )

    comparator_task.kill()
