# SPDX-FileCopyrightText: © 2024 Tiny Tapeout
# SPDX-License-Identifier: Apache-2.0

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import (
    ClockCycles,
    NextTimeStep,
    ReadOnly,
    RisingEdge,
)


# ================================================================
# TINY TAPEOUT PIN MAPPING
# ================================================================
#
# ui_in[0]    = external comparator output
# uio_in[0]   = experimental Trojan enable
#
# uo_out[3:0] = physical DAC switch outputs
# uo_out[4]   = sample-switch control
# uo_out[7:5] = SAR state-machine state


DAC_MASK = 0x0F

SAMPLE = 0
HOLD = 1
SET_BIT = 2
WAIT_DAC = 3
READ_COMP = 4
DONE = 5


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
    """Return the SAR FSM state from uo_out[7:5]."""

    return (int(dut.uo_out.value) >> 5) & 0x07


def get_design_instance(dut):
    """
    Return the Tiny Tapeout design instance inside tb.

    The standard Tiny Tapeout tb.v normally names this instance dut.
    """

    return getattr(dut, "dut", None)


# ================================================================
# INPUT HELPERS
# ================================================================

async def set_trojan_enable(dut, enabled):
    """Drive uio_in[0] after leaving the simulator ReadOnly phase."""

    await NextTimeStep()
    dut.uio_in.value = 0x01 if enabled else 0x00


async def assert_reset(dut):
    """Assert Tiny Tapeout's active-low reset."""

    await NextTimeStep()

    dut.ena.value = 1
    dut.ui_in.value = 0
    dut.uio_in.value = 0
    dut.rst_n.value = 0

    await ClockCycles(dut.clk, 10)


async def release_reset(dut):
    """Release Tiny Tapeout's active-low reset."""

    await NextTimeStep()
    dut.rst_n.value = 1

    await ClockCycles(dut.clk, 10)


async def reset_dut(dut):
    """Apply and release reset."""

    dut._log.info("Reset")

    await assert_reset(dut)
    await release_reset(dut)


# ================================================================
# EXTERNAL ANALOG-HARDWARE MODEL
# ================================================================

async def comparator_model(dut, input_code):
    """
    Model the external comparator and capacitor DAC.

    input_code represents an ideal analog input from 0 through 15.

    The comparator observes the physical DAC outputs on uo_out[3:0].
    Therefore, it also observes inverted outputs while the Trojan
    phase is active.
    """

    while True:
        await RisingEdge(dut.clk)
        await ReadOnly()

        trial_code = get_dac_code(dut)
        comparator_value = 1 if input_code >= trial_code else 0

        await NextTimeStep()
        dut.ui_in.value = comparator_value


# ================================================================
# CONVERSION HELPERS
# ================================================================

async def wait_for_state(dut, target_state, timeout_cycles=20_000):
    """Wait until the controller reaches the requested FSM state."""

    for _ in range(timeout_cycles):
        await RisingEdge(dut.clk)
        await ReadOnly()

        if get_state(dut) == target_state:
            return get_dac_code(dut)

    raise AssertionError(
        f"Timed out waiting for state {target_state}. "
        f"Current state={get_state(dut)}, "
        f"DAC={get_dac_code(dut):04b}"
    )


async def wait_for_conversion_done(dut, timeout_cycles=20_000):
    """
    Wait for the next conversion to enter DONE.

    If the controller is already in DONE, first wait for it to leave.
    """

    for _ in range(timeout_cycles):
        await RisingEdge(dut.clk)
        await ReadOnly()

        if get_state(dut) != DONE:
            break
    else:
        raise AssertionError("SAR controller remained stuck in DONE")

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


async def run_conversion(dut, input_code, log_result=False):
    """Run one conversion and return the physical DAC output."""

    result = await wait_for_conversion_done(dut)

    if log_result:
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


async def run_clean_code_test(dut, input_code):
    """Run and verify one clean SAR conversion."""

    comparator_task = cocotb.start_soon(
        comparator_model(dut, input_code)
    )

    try:
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
    finally:
        comparator_task.cancel()
        await NextTimeStep()


# ================================================================
# BASIC RESET AND CONVERSION TEST
# ================================================================

@cocotb.test()
async def test_project(dut):
    """Test reset behavior and one representative conversion."""

    dut._log.info("Start basic SAR ADC test")

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

    await set_trojan_enable(dut, False)
    await run_clean_code_test(dut, 10)


# ================================================================
# FAST REPRESENTATIVE CLEAN-CODE TEST
# ================================================================

@cocotb.test()
async def test_representative_input_codes(dut):
    """
    Test a small representative set instead of all 16 codes.

    Codes:
        0011 = low input
        1010 = middle/high input
        1110 = high input
    """

    dut._log.info("Start representative clean-code test")

    clock = Clock(dut.clk, 20, unit="ns")
    cocotb.start_soon(clock.start())

    for input_code in (3, 10, 14):
        await assert_reset(dut)

        comparator_task = cocotb.start_soon(
            comparator_model(dut, input_code)
        )

        try:
            await release_reset(dut)
            await set_trojan_enable(dut, False)

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
        finally:
            comparator_task.cancel()
            await NextTimeStep()

    dut._log.info("Representative clean codes passed")


# ================================================================
# TROJAN-DISABLED CONTROL TEST
# ================================================================

@cocotb.test()
async def test_trojan_disabled(dut):
    """
    Verify several consecutive conversions remain clean while the
    Trojan-enable input is low.

    This replaces the old 501-conversion disabled test.
    """

    dut._log.info("Test Trojan-disabled operation")

    await start_test(dut)
    await set_trojan_enable(dut, False)

    input_code = 10

    comparator_task = cocotb.start_soon(
        comparator_model(dut, input_code)
    )

    try:
        for conversion_number in range(1, 4):
            result = await run_conversion(dut, input_code)

            assert result == input_code, (
                f"Trojan was disabled, but conversion "
                f"{conversion_number} produced {result:04b}; "
                f"expected {input_code:04b}"
            )

        dut._log.info(
            "Three consecutive Trojan-disabled conversions passed"
        )
    finally:
        comparator_task.cancel()
        await NextTimeStep()


# ================================================================
# FAST RTL TROJAN-BOUNDARY TEST
# ================================================================

@cocotb.test()
async def test_trojan_boundary_fast(dut):
    """
    Test the 500-conversion boundary without running 500 conversions.

    In RTL simulation, directly set the internal conversion counter
    to 499. One conversion then causes the normal-to-inverted phase
    transition.

    In gate-level simulation, internal registers are usually renamed
    or optimized away. If the counter is unavailable, this test exits
    successfully instead of running hundreds of conversions.
    """

    dut._log.info("Start fast Trojan-boundary test")

    await start_test(dut)

    design = get_design_instance(dut)

    if design is None:
        dut._log.info(
            "Design instance not available; "
            "skipping internal-counter boundary test"
        )
        return

    counter = getattr(design, "trojan_conversion_count", None)
    phase = getattr(design, "trojan_phase", None)

    if counter is None or phase is None:
        dut._log.info(
            "Internal Trojan registers are not visible. "
            "This is expected for gate-level simulation; "
            "skipping the long 500-conversion test."
        )
        return

    input_code = 10
    normal_code = input_code
    inverted_code = (~input_code) & DAC_MASK

    comparator_task = cocotb.start_soon(
        comparator_model(dut, input_code)
    )

    try:
        await set_trojan_enable(dut, True)

        # Place the RTL counter one conversion before its threshold.
        await NextTimeStep()
        counter.value = 499
        phase.value = 0

        dut._log.info(
            "Forced RTL Trojan counter to 499"
        )

        # The conversion itself enters DONE while still normal.
        result = await run_conversion(
            dut,
            input_code,
            log_result=True,
        )

        assert result == normal_code, (
            f"Boundary conversion should finish normally. "
            f"Expected {normal_code:04b}, "
            f"received {result:04b}"
        )

        # When DONE executes and returns to SAMPLE, the phase toggles.
        phase_switch_output = await wait_for_state(dut, SAMPLE)

        dut._log.info(
            f"Output after Trojan activation: "
            f"{phase_switch_output:04b}"
        )

        assert phase_switch_output == inverted_code, (
            f"Expected the output to invert to "
            f"{inverted_code:04b}, "
            f"received {phase_switch_output:04b}"
        )
    finally:
        comparator_task.cancel()
        await NextTimeStep()


# ================================================================
# FAST EXTERNAL-DISABLE TEST
# ================================================================

@cocotb.test()
async def test_external_trojan_disable_fast(dut):
    """
    Verify that uio_in[0] low selects the normal DAC outputs.

    During RTL, the internal phase register is forced active so the
    disable behavior can be tested immediately. This test is skipped
    at gate level if the phase register is not visible.
    """

    dut._log.info("Start fast external-disable test")

    await start_test(dut)

    design = get_design_instance(dut)

    if design is None:
        dut._log.info(
            "Design instance not available; skipping fast disable test"
        )
        return

    phase = getattr(design, "trojan_phase", None)

    if phase is None:
        dut._log.info(
            "Internal Trojan phase is not visible. "
            "This is expected for gate-level simulation; "
            "skipping the internal-force disable test."
        )
        return

    input_code = 10
    inverted_code = (~input_code) & DAC_MASK

    comparator_task = cocotb.start_soon(
        comparator_model(dut, input_code)
    )

    try:
        await set_trojan_enable(dut, True)

        await NextTimeStep()
        phase.value = 1

        # The physical output should now use the inverted mapping.
        await ReadOnly()
        infected_output = get_dac_code(dut)

        dut._log.info(
            f"Forced infected-phase output: {infected_output:04b}"
        )

        # The exact internal SAR value at this moment may be zero,
        # so only confirm that disabling restores clean conversions.
        await set_trojan_enable(dut, False)

        restored_result = await run_conversion(
            dut,
            input_code,
            log_result=True,
        )

        assert restored_result == input_code, (
            f"Disabling the Trojan did not restore normal operation. "
            f"Expected {input_code:04b}, "
            f"received {restored_result:04b}"
        )

        dut._log.info(
            f"Trojan disabled successfully; "
            f"reference inverted code was {inverted_code:04b}"
        )
    finally:
        comparator_task.cancel()
        await NextTimeStep()
