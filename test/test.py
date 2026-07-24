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
# Automatic Trojan sequence:
#
#   Counter 0-449   = normal DAC output
#   Counter 450-499 = infected DAC output
#   Counter wraps back to zero
#


DAC_MASK = 0x0F

SAMPLE = 0
HOLD = 1
SET_BIT = 2
WAIT_DAC = 3
READ_COMP = 4
DONE = 5

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
# RESET HELPERS
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

    dut._log.info("Resetting design")

    await assert_reset(dut)
    await release_reset(dut)


async def start_test(dut):
    """Start the clock and reset the design."""

    clock = Clock(
        dut.clk,
        10,
        unit="ns",
    )

    cocotb.start_soon(clock.start())

    await reset_dut(dut)


# ================================================================
# COMPARATOR MODEL
# ================================================================

async def comparator_model(dut, input_code):
    """
    Model the external comparator and capacitor DAC.

    The model updates only when uo_out changes. This is much faster
    than checking the DAC output on every main clock cycle.

    Comparator HIGH means:

        input_code >= physical DAC code
    """

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

        await NextTimeStep()

        dut.ui_in.value = comparator_value


# ================================================================
# STATE HELPERS
# ================================================================

async def wait_for_output_change(dut):
    """
    Wait for the next visible output change.

    The timeout prevents a broken design from running forever.
    """

    await with_timeout(
        ValueChange(dut.uo_out),
        STATE_CHANGE_TIMEOUT_US,
        "us",
    )

    await ReadOnly()


async def wait_for_state(
    dut,
    target_state,
    max_changes=40,
):
    """
    Wait for the controller to reach target_state.

    Return the physical DAC code observed in that state.
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
    Wait for the next completed SAR conversion.

    If the FSM is already in DONE, wait for it to leave DONE first.
    """

    if get_state(dut) == DONE:

        while get_state(dut) == DONE:
            await wait_for_output_change(dut)

    return await wait_for_state(
        dut,
        DONE,
    )


async def run_conversion(
    dut,
    input_code,
    log_result=False,
):
    """Run one conversion and return its physical DAC output."""

    result = await wait_for_conversion_done(dut)

    if log_result:

        dut._log.info(
            f"Input={input_code:04b} ({input_code}), "
            f"output={result:04b} ({result})"
        )

    return result


# ================================================================
# INTERNAL TROJAN COUNTER HELPER
# ================================================================

def get_trojan_counter(dut):
    """
    Locate the internal Trojan conversion counter.

    The exact hierarchy depends on the Tiny Tapeout simulation
    wrapper and the name used in the Verilog source.
    """

    possible_names = (
        "trojan_count",
        "trojan_conversion_count",
    )

    # Counter directly inside the simulation top level.
    for signal_name in possible_names:

        if hasattr(dut, signal_name):
            return getattr(dut, signal_name)

    # Common Tiny Tapeout wrapper instance names.
    possible_instances = (
        "user_project",
        "uut",
        "dut",
        "project",
    )

    for instance_name in possible_instances:

        if not hasattr(dut, instance_name):
            continue

        instance = getattr(dut, instance_name)

        for signal_name in possible_names:

            if hasattr(instance, signal_name):
                return getattr(instance, signal_name)

    raise AssertionError(
        "Could not locate the internal Trojan counter. "
        "The counter should be named trojan_count or "
        "trojan_conversion_count and must be visible in RTL "
        "simulation."
    )


async def set_trojan_counter(
    dut,
    value,
):
    """Set the internal Trojan counter during RTL simulation."""

    counter = get_trojan_counter(dut)

    await NextTimeStep()

    counter.value = value

    await NextTimeStep()

    actual_value = int(counter.value)

    assert actual_value == value, (
        f"Could not set Trojan counter to {value}. "
        f"Counter remained at {actual_value}."
    )

    dut._log.info(
        f"Trojan counter set to {value}"
    )


# ================================================================
# BASIC FUNCTION TEST
# ================================================================

@cocotb.test()
async def test_project(dut):
    """Test reset and one normal SAR conversion."""

    dut._log.info(
        "Start basic SAR ADC test"
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
        comparator_model(
            dut,
            input_code,
        )
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

    dut._log.info(
        "Basic SAR ADC test passed"
    )


# ================================================================
# SHORT CLEAN INPUT SWEEP
# ================================================================

@cocotb.test()
async def test_selected_input_codes(dut):
    """
    Test a small selection of input values.

    Testing zero, intermediate values, and full scale is sufficient
    for a quick simulation. This avoids resetting and testing all
    16 possible input codes.
    """

    dut._log.info(
        "Start selected input-code test"
    )

    clock = Clock(
        dut.clk,
        10,
        unit="ns",
    )

    cocotb.start_soon(clock.start())

    test_codes = (
        0,
        5,
        10,
        15,
    )

    for input_code in test_codes:

        await assert_reset(dut)

        comparator_task = cocotb.start_soon(
            comparator_model(
                dut,
                input_code,
            )
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
        "Selected input codes passed"
    )


# ================================================================
# SHORT AUTOMATIC TROJAN TEST
# ================================================================

@cocotb.test()
async def test_automatic_trojan_window(dut):
    """
    Test the automatic Trojan without running 500 conversions.

    The test directly sets the internal counter near each important
    boundary.

    Test sequence:

        Counter 449:
            Run the final normal conversion.

        Counter advances to 450:
            Confirm the infected output activates.

        Counter 450:
            Run one infected conversion.

        Counter 499:
            Run the final infected conversion.

        Counter wraps to zero:
            Run one restored normal conversion.
    """

    dut._log.info(
        "Start shortened automatic Trojan test"
    )

    await start_test(dut)

    input_code = 10
    normal_code = input_code
    inverted_code = (~input_code) & DAC_MASK

    comparator_task = cocotb.start_soon(
        comparator_model(
            dut,
            input_code,
        )
    )

    # ------------------------------------------------------------
    # TEST THE START OF THE INFECTED WINDOW
    # ------------------------------------------------------------

    await set_trojan_counter(
        dut,
        449,
    )

    # Counter 449 is still normal.
    result_450 = await run_conversion(
        dut,
        input_code,
        log_result=True,
    )

    assert result_450 == normal_code, (
        f"Final normal conversion failed. "
        f"Expected {normal_code:04b}, "
        f"received {result_450:04b}"
    )

    # DONE increments the counter from 449 to 450.
    # The Trojan should now activate.
    infected_boundary = await wait_for_state(
        dut,
        SAMPLE,
    )

    dut._log.info(
        f"Infected boundary output: "
        f"{infected_boundary:04b}"
    )

    assert infected_boundary == inverted_code, (
        f"Trojan did not activate at count 450. "
        f"Expected physical output "
        f"{inverted_code:04b}, "
        f"received {infected_boundary:04b}"
    )

    # Run one complete infected conversion.
    infected_result = await run_conversion(
        dut,
        input_code,
        log_result=True,
    )

    dut._log.info(
        f"Infected conversion result: "
        f"{infected_result:04b}"
    )

    # The infected conversion does not need to equal one exact code.
    # The inverted physical DAC is part of the comparator feedback
    # loop, so it changes the SAR decision process.

    # ------------------------------------------------------------
    # TEST THE END OF THE INFECTED WINDOW
    # ------------------------------------------------------------

    await set_trojan_counter(
        dut,
        499,
    )

    # Run the final conversion while the Trojan is active.
    final_infected_result = await run_conversion(
        dut,
        input_code,
        log_result=True,
    )

    dut._log.info(
        f"Final infected conversion result: "
        f"{final_infected_result:04b}"
    )

    # DONE should wrap the counter from 499 back to zero.
    restored_boundary = await wait_for_state(
        dut,
        SAMPLE,
    )

    dut._log.info(
        f"Output after counter wrap: "
        f"{restored_boundary:04b}"
    )

    # The first complete conversion after the wrap must be clean.
    restored_result = await run_conversion(
        dut,
        input_code,
        log_result=True,
    )

    assert restored_result == normal_code, (
        f"Normal operation was not restored. "
        f"Expected {normal_code:04b}, "
        f"received {restored_result:04b}"
    )

    comparator_task.cancel()

    dut._log.info(
        "Short automatic Trojan test passed"
    )
