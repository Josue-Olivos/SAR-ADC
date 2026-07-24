# SPDX-FileCopyrightText: © 2024 Tiny Tapeout
# SPDX-License-Identifier: Apache-2.0

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import (
    ClockCycles,
    NextTimeStep,
    ReadOnly,
    ValueChange,
    with_timeout,
)


# ================================================================
# TINY TAPEOUT PIN MAPPING
# ================================================================
#
# ui_in[0]    = external comparator output
#
# uo_out[3:0] = physical DAC switch outputs
# uo_out[4]   = sample-switch control
# uo_out[7:5] = SAR state-machine state
#
# The automatic Trojan is internal:
#
#   conversions 1-450   = normal
#   conversions 451-500 = infected
#   sequence repeats


DAC_MASK = 0x0F

SAMPLE = 0
HOLD = 1
SET_BIT = 2
WAIT_DAC = 3
READ_COMP = 4
DONE = 5

# Generous timeout for one visible output/state transition.
# The RTL uses a divided ADC clock, so output changes are much less
# frequent than clk edges.
STATE_CHANGE_TIMEOUT_US = 2_000


# ================================================================
# OUTPUT HELPERS
# ================================================================

def get_dac_code(dut):
    """Return the physical 4-bit DAC output from uo_out[3:0]."""

    return int(dut.uo_out.value) & DAC_MASK


def get_sample_switch(dut):
    """Return the sample-switch output from uo_out[4]."""

    return (int(dut.uo_out.value) >> 4) & 0x01


def get_state(dut):
    """Return the SAR state from uo_out[7:5]."""

    return (int(dut.uo_out.value) >> 5) & 0x07


# ================================================================
# RESET AND STARTUP
# ================================================================

async def assert_reset(dut):
    """Assert the active-low Tiny Tapeout reset."""

    await NextTimeStep()

    dut.ena.value = 1
    dut.ui_in.value = 0
    dut.uio_in.value = 0
    dut.rst_n.value = 0

    await ClockCycles(dut.clk, 10)


async def release_reset(dut):
    """Release the active-low Tiny Tapeout reset."""

    await NextTimeStep()

    dut.rst_n.value = 1

    await ClockCycles(dut.clk, 10)


async def reset_dut(dut):
    """Apply and release reset."""

    dut._log.info("Reset")

    await assert_reset(dut)
    await release_reset(dut)


async def start_test(dut):
    """
    Start the simulation clock and reset the design.

    A 10 ns testbench clock is used. The design still generates its
    adc_tick from its internal divider, but the testbench no longer
    wakes up on every clock edge.
    """

    clock = Clock(dut.clk, 10, unit="ns")
    cocotb.start_soon(clock.start())

    await reset_dut(dut)


# ================================================================
# FAST EXTERNAL COMPARATOR MODEL
# ================================================================

async def comparator_model(dut, input_code):
    """
    Behavioral model of the external comparator and capacitor DAC.

    This task waits for uo_out to change instead of waking up at every
    high-frequency clk edge.

    The physical DAC and FSM outputs only change on adc_tick, so this
    removes millions of unnecessary Python callbacks during long RTL
    and gate-level simulations.

    Comparator HIGH means:

        input_code >= physical DAC code
    """

    # Drive an initial comparator value.
    await NextTimeStep()

    dut.ui_in.value = (
        1 if input_code >= get_dac_code(dut) else 0
    )

    while True:
        await ValueChange(dut.uo_out)
        await ReadOnly()

        trial_code = get_dac_code(dut)

        comparator_value = (
            1 if input_code >= trial_code else 0
        )

        # Leave ReadOnly before driving the input.
        await NextTimeStep()

        dut.ui_in.value = comparator_value


# ================================================================
# FAST STATE/CONVERSION HELPERS
# ================================================================

async def wait_for_output_change(dut):
    """
    Wait for the next visible FSM or DAC output change.

    with_timeout prevents a broken design from hanging forever.
    """

    await with_timeout(
        ValueChange(dut.uo_out),
        STATE_CHANGE_TIMEOUT_US,
        "us",
    )

    await ReadOnly()


async def wait_for_state(dut, target_state, max_changes=40):
    """
    Wait until the controller reaches target_state.

    This observes uo_out changes rather than every clk edge.
    """

    if get_state(dut) == target_state:
        return get_dac_code(dut)

    for _ in range(max_changes):
        await wait_for_output_change(dut)

        if get_state(dut) == target_state:
            return get_dac_code(dut)

    raise AssertionError(
        f"Timed out waiting for state {target_state}. "
        f"Current state={get_state(dut)}, "
        f"DAC={get_dac_code(dut):04b}"
    )


async def wait_for_conversion_done(dut):
    """
    Wait for the next complete SAR conversion.

    If already in DONE, first wait for the controller to leave DONE.
    """

    if get_state(dut) == DONE:
        while get_state(dut) == DONE:
            await wait_for_output_change(dut)

    return await wait_for_state(dut, DONE)


async def run_conversion(
    dut,
    input_code,
    log_result=False,
):
    """Wait for one conversion and return its physical DAC output."""

    result = await wait_for_conversion_done(dut)

    if log_result:
        dut._log.info(
            f"Input={input_code:04b} ({input_code}), "
            f"output={result:04b} ({result})"
        )

    return result


# ================================================================
# BASIC SAR ADC TEST
# ================================================================

@cocotb.test()
async def test_project(dut):
    """Test reset behavior and one normal conversion."""

    dut._log.info(
        "Start basic automatic-Trojan SAR ADC test"
    )

    await start_test(dut)

    assert get_state(dut) == SAMPLE, (
        f"Expected SAMPLE after reset, "
        f"but state was {get_state(dut)}"
    )

    assert get_sample_switch(dut) == 1, (
        "Sample switch should be enabled after reset"
    )

    assert get_dac_code(dut) == 0, (
        f"DAC should be 0000 after reset, "
        f"but was {get_dac_code(dut):04b}"
    )

    input_code = 10

    comparator_task = cocotb.start_soon(
        comparator_model(dut, input_code)
    )

    result = await run_conversion(
        dut,
        input_code,
        log_result=True,
    )

    assert result == input_code, (
        f"Expected {input_code:04b}, "
        f"but received {result:04b}"
    )

    comparator_task.cancel()


# ================================================================
# CLEAN INPUT-CODE SWEEP
# ================================================================

@cocotb.test()
async def test_all_input_codes(dut):
    """Test all 16 input codes before the automatic trigger."""

    dut._log.info(
        "Start complete clean 4-bit SAR sweep"
    )

    clock = Clock(dut.clk, 10, unit="ns")
    cocotb.start_soon(clock.start())

    for input_code in range(16):

        await assert_reset(dut)

        comparator_task = cocotb.start_soon(
            comparator_model(dut, input_code)
        )

        await release_reset(dut)

        result = await run_conversion(
            dut,
            input_code,
            log_result=True,
        )

        assert result == input_code, (
            f"Input {input_code:04b}: "
            f"expected {input_code:04b}, "
            f"received {result:04b}"
        )

        comparator_task.cancel()

        await NextTimeStep()

    dut._log.info(
        "All 16 clean input codes passed"
    )


# ================================================================
# AUTOMATIC TROJAN WINDOW TEST
# ================================================================

@cocotb.test()
async def test_automatic_trojan_window(dut):
    """
    Verify the automatic 450-normal / 50-infected sequence.

    Counter behavior:

        conversions 1-450:
            normal physical DAC behavior

        after conversion 450:
            the DONE state advances the counter to 450,
            activating the Trojan for the next conversion

        conversions 451-500:
            infected physical DAC behavior

        after conversion 500:
            the counter wraps to zero and normal behavior returns

        conversion 501:
            first full clean conversion after the infected window
    """

    dut._log.info(
        "Start automatic 450-normal / "
        "50-infected Trojan test"
    )

    await start_test(dut)

    input_code = 10
    normal_code = input_code
    inverted_code = (~input_code) & DAC_MASK

    comparator_task = cocotb.start_soon(
        comparator_model(dut, input_code)
    )

    # ------------------------------------------------------------
    # Conversions 1 through 449 must remain normal.
    # ------------------------------------------------------------

    for conversion_number in range(1, 450):

        result = await run_conversion(
            dut,
            input_code,
        )

        assert result == normal_code, (
            f"Conversion {conversion_number} should be normal. "
            f"Expected {normal_code:04b}, "
            f"received {result:04b}"
        )

        if conversion_number % 100 == 0:
            dut._log.info(
                f"Verified normal conversion "
                f"{conversion_number}"
            )

    # ------------------------------------------------------------
    # Conversion 450 completes before the counter activates
    # the Trojan payload.
    # ------------------------------------------------------------

    result_450 = await run_conversion(
        dut,
        input_code,
        log_result=True,
    )

    assert result_450 == normal_code, (
        f"Conversion 450 should finish normally. "
        f"Expected {normal_code:04b}, "
        f"received {result_450:04b}"
    )

    # ------------------------------------------------------------
    # DONE executes, the count becomes 450, and the physical
    # DAC output immediately becomes inverted.
    # ------------------------------------------------------------

    infected_boundary = await wait_for_state(
        dut,
        SAMPLE,
    )

    dut._log.info(
        f"Output when infected window begins: "
        f"{infected_boundary:04b}"
    )

    assert infected_boundary == inverted_code, (
        f"Expected boundary output "
        f"{inverted_code:04b}, "
        f"received {infected_boundary:04b}"
    )

    # ------------------------------------------------------------
    # Conversions 451 through 499 are infected.
    #
    # Their exact final codes are not required because the
    # inverted physical DAC is also inside the comparator
    # feedback loop.
    # ------------------------------------------------------------

    for conversion_number in range(451, 500):

        await run_conversion(
            dut,
            input_code,
        )

        if conversion_number % 10 == 0:
            dut._log.info(
                f"Advanced through infected conversion "
                f"{conversion_number}"
            )

    # ------------------------------------------------------------
    # Conversion 500 is the final infected conversion.
    # ------------------------------------------------------------

    result_500 = await run_conversion(
        dut,
        input_code,
        log_result=True,
    )

    dut._log.info(
        f"Final infected conversion result: "
        f"{result_500:04b}"
    )

    # ------------------------------------------------------------
    # DONE executes, wraps the counter to zero, and restores
    # normal output behavior.
    # ------------------------------------------------------------

    restored_boundary = await wait_for_state(
        dut,
        SAMPLE,
    )

    dut._log.info(
        f"Output immediately after normal mode returns: "
        f"{restored_boundary:04b}"
    )

    # ------------------------------------------------------------
    # Conversion 501 must be fully clean again.
    # ------------------------------------------------------------

    result_501 = await run_conversion(
        dut,
        input_code,
        log_result=True,
    )

    assert result_501 == normal_code, (
        f"Expected clean operation on conversion 501. "
        f"Expected {normal_code:04b}, "
        f"received {result_501:04b}"
    )

    comparator_task.cancel()

    dut._log.info(
        "Automatic infected window activated "
        "and cleared correctly"
    )
